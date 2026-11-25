"""A command that lands while a poll is running.

Seen on a real system: a zone was switched on while the regular poll was half way
through. It showed on, off again 0.3 seconds later, and on after ten seconds; the
amplifier had switched on at once.

The poll had read that zone before the command. It holds the refresh lock, so the
refresh the command asks for is put off to the end of Home Assistant's ten-second
cooldown, and the poll's older answer replaced the new state until then.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.xantech.const import DEFAULT_SCAN_INTERVAL, DOMAIN
from custom_components.xantech.coordinator import MAX_POLL_PASSES, XantechCoordinator

PORT = '/dev/ttyUSB0'
AMP = 'dax88__dev_ttyusb0'
ZONES = [11, 12, 13]


def _setter(key: str) -> Callable[[_Amp, int, Any], Awaitable[None]]:
    async def set_value(self: _Amp, zone: int, value: Any) -> None:
        self.zones[zone][key] = value

    return set_value


class _Amp:
    """A stand-in amplifier whose poll can be stopped half way."""

    def __init__(self) -> None:
        self.zones: dict[int, dict[str, Any]] = {
            zone: {
                'power': False,
                'volume': 10,
                'mute': False,
                'source': 1,
                'bass': 7,
                'treble': 7,
                'balance': 10,
            }
            for zone in ZONES
        }
        self.reads: list[int] = []
        self.stop_at: int | None = None
        self.stopped = asyncio.Event()
        self.go_on = asyncio.Event()
        self.after_read: Callable[[int], Awaitable[None]] | None = None

    async def zone_status(self, zone: int) -> dict[str, Any]:
        status = dict(self.zones[zone])  # what the amplifier says at this moment
        self.reads.append(zone)
        if zone == self.stop_at:
            # the answer is on its way; a command gets in before the next read
            self.stop_at = None
            self.stopped.set()
            await self.go_on.wait()
        if self.after_read is not None:
            await self.after_read(zone)
        return status

    set_power = _setter('power')
    set_volume = _setter('volume')
    set_mute = _setter('mute')
    set_source = _setter('source')
    set_bass = _setter('bass')
    set_treble = _setter('treble')
    set_balance = _setter('balance')

    def close(self) -> None:
        """Nothing to close."""


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title='Hi-Fi',
        version=1,
        minor_version=1,
        unique_id=f'{DOMAIN}_{PORT}',
        data={
            'amp_type': 'dax88',
            'enable_audio_controls': True,
            'port': PORT,
            'sources': {'1': {'name': 'Streamer'}},
            'zones': {str(zone): {'name': f'Room {zone}'} for zone in ZONES},
        },
        options={},
    )


async def _poll_stopped_half_way(
    hass: HomeAssistant, amp: _Amp, coordinator: XantechCoordinator
) -> asyncio.Event:
    """Start the regular poll and hold it after zone 11 was read.

    Returns an event that is set when the poll has reported.
    """
    reported = asyncio.Event()
    coordinator.async_add_listener(reported.set)
    amp.stop_at = 12
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
    )
    await asyncio.wait_for(amp.stopped.wait(), 1)
    assert amp.reads[-2:] == [11, 12]
    return reported


def _watch(hass: HomeAssistant, entity_id: str) -> list[str]:
    seen: list[str] = []

    @callback
    def changed(event: Event) -> None:
        seen.append(event.data['new_state'].state)

    async_track_state_change_event(hass, [entity_id], changed)
    return seen


async def test_a_zone_switched_on_during_a_poll_never_shows_off_again(
    hass: HomeAssistant,
) -> None:
    """The live sequence, through Home Assistant: service call, regular poll, states."""
    amp, entry = _Amp(), _entry()
    entry.add_to_hass(hass)
    with patch(
        'custom_components.xantech.async_get_amp_controller',
        AsyncMock(return_value=amp),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        player = er.async_get(hass).async_get_entity_id(
            'media_player', DOMAIN, f'{DOMAIN}_{AMP}_zone_11'
        )
        assert player is not None
        assert hass.states.get(player).state == 'off'
        seen = _watch(hass, player)

        reported = await _poll_stopped_half_way(
            hass, amp, entry.runtime_data.coordinator
        )
        await hass.services.async_call(
            'media_player', 'turn_on', {'entity_id': player}, blocking=True
        )
        assert amp.zones[11]['power'] is True
        assert hass.states.get(player).state == 'on'

        amp.go_on.set()
        await asyncio.wait_for(reported.wait(), 1)
        await hass.async_block_till_done()

        assert seen == ['on']
        assert hass.states.get(player).state == 'on'
        assert entry.runtime_data.coordinator.data[11]['power'] is True

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_a_tone_control_changed_during_a_poll_never_shows_the_old_value(
    hass: HomeAssistant,
) -> None:
    """The tone sliders take the same path."""
    amp, entry = _Amp(), _entry()
    entry.add_to_hass(hass)
    with patch(
        'custom_components.xantech.async_get_amp_controller',
        AsyncMock(return_value=amp),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        bass = er.async_get(hass).async_get_entity_id(
            'number', DOMAIN, f'{DOMAIN}_{AMP}_zone_11_bass'
        )
        assert bass is not None
        before = hass.states.get(bass)
        highest = before.attributes['max']
        assert float(before.state) != float(highest)
        seen = _watch(hass, bass)

        reported = await _poll_stopped_half_way(
            hass, amp, entry.runtime_data.coordinator
        )
        await hass.services.async_call(
            'number', 'set_value', {'entity_id': bass, 'value': highest}, blocking=True
        )
        assert amp.zones[11]['bass'] != 7

        amp.go_on.set()
        await asyncio.wait_for(reported.wait(), 1)
        await hass.async_block_till_done()

        assert len(seen) == 1
        assert float(seen[0]) == float(highest)
        assert float(hass.states.get(bass).state) == float(highest)

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_the_poll_reads_again_once_and_reports_the_new_state(
    hass: HomeAssistant,
) -> None:
    """The coordinator alone: what the poll reports, and how often it reads."""
    amp = _Amp()
    coordinator = XantechCoordinator(hass, amp, 'test_amp', ZONES)
    try:
        await coordinator.async_refresh()
        assert amp.reads == ZONES  # no command: every zone is read once
        assert coordinator.data[11]['power'] is False

        amp.reads.clear()
        amp.stop_at = 12
        poll = hass.async_create_task(coordinator.async_refresh())
        await asyncio.wait_for(amp.stopped.wait(), 1)
        await coordinator.async_set_zone_power(11, True)
        amp.go_on.set()
        await poll

        assert coordinator.data[11]['power'] is True
        assert amp.reads == ZONES + ZONES
    finally:
        await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ('command', 'setting', 'value'),
    [
        ('async_set_zone_power', 'power', True),
        ('async_set_zone_source', 'source', 2),
        ('async_set_zone_volume', 'volume', 21),
        ('async_set_zone_mute', 'mute', True),
        ('async_set_zone_bass', 'bass', 3),
        ('async_set_zone_treble', 'treble', 11),
        ('async_set_zone_balance', 'balance', 4),
    ],
)
async def test_every_command_makes_a_running_poll_read_again(
    hass: HomeAssistant, command: str, setting: str, value: Any
) -> None:
    amp = _Amp()
    coordinator = XantechCoordinator(hass, amp, 'test_amp', ZONES)
    try:
        await coordinator.async_refresh()
        assert coordinator.data[11][setting] != value

        amp.stop_at = 12
        poll = hass.async_create_task(coordinator.async_refresh())
        await asyncio.wait_for(amp.stopped.wait(), 1)
        await getattr(coordinator, command)(11, value)
        amp.go_on.set()
        await poll

        assert coordinator.data[11][setting] == value
    finally:
        await coordinator.async_shutdown()


async def test_commands_that_keep_coming_do_not_hold_the_poll_for_ever(
    hass: HomeAssistant,
) -> None:
    """A slider that is being dragged sends a command into every pass."""
    amp = _Amp()
    coordinator = XantechCoordinator(hass, amp, 'test_amp', ZONES)
    volume = 0

    async def another_command(zone: int) -> None:
        nonlocal volume
        if zone == ZONES[-1]:
            volume += 1
            await coordinator.async_set_zone_volume(11, volume)

    try:
        amp.after_read = another_command
        await coordinator.async_refresh()

        assert amp.reads == ZONES * MAX_POLL_PASSES
        assert coordinator.last_update_success
        assert volume == MAX_POLL_PASSES
    finally:
        await coordinator.async_shutdown()


async def test_a_drag_that_outlasts_the_poll_ends_on_the_last_value(
    hass: HomeAssistant,
) -> None:
    """Seen on a real system: a volume slider dragged right through a poll.

    Every pass was followed by another command, so the last pass was older than the
    last command too. The slider must stay where it was put, not jump back to what
    that pass read, and follow the amplifier again at the next refresh.
    """
    amp, entry = _Amp(), _entry()
    entry.add_to_hass(hass)
    with patch(
        'custom_components.xantech.async_get_amp_controller',
        AsyncMock(return_value=amp),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        player = er.async_get(hass).async_get_entity_id(
            'media_player', DOMAIN, f'{DOMAIN}_{AMP}_zone_11'
        )
        assert player is not None
        await hass.services.async_call(
            'media_player', 'turn_on', {'entity_id': player}, blocking=True
        )
        coordinator = entry.runtime_data.coordinator
        await coordinator.async_refresh()

        def shown() -> int:
            return round(hass.states.get(player).attributes['volume_level'] * 38)

        assert shown() == 10
        dragged: list[int] = []

        async def drag_on(zone: int) -> None:
            # another step of the slider at the end of every pass
            if zone == ZONES[-1]:
                dragged.append(20 + len(dragged))
                await hass.services.async_call(
                    'media_player',
                    'volume_set',
                    {'entity_id': player, 'volume_level': dragged[-1] / 38},
                    blocking=True,
                )

        amp.after_read = drag_on
        await coordinator.async_refresh()
        amp.after_read = None
        await hass.async_block_till_done()

        assert len(dragged) == MAX_POLL_PASSES
        assert amp.zones[11]['volume'] == dragged[-1]
        assert coordinator.data[11]['volume'] == dragged[-2]  # what the last pass read
        assert shown() == dragged[-1]

        # the next refresh is not overtaken: the amplifier is followed again
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert shown() == dragged[-1]
        amp.zones[11]['volume'] = 5  # changed at the amplifier itself
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert shown() == 5

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_a_tone_slider_dragged_through_the_poll_ends_on_the_last_value(
    hass: HomeAssistant,
) -> None:
    amp, entry = _Amp(), _entry()
    entry.add_to_hass(hass)
    with patch(
        'custom_components.xantech.async_get_amp_controller',
        AsyncMock(return_value=amp),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        bass = er.async_get(hass).async_get_entity_id(
            'number', DOMAIN, f'{DOMAIN}_{AMP}_zone_11_bass'
        )
        assert bass is not None
        coordinator = entry.runtime_data.coordinator
        attributes = hass.states.get(bass).attributes
        low, step = attributes['min'], attributes['step']
        dragged: list[float] = []

        async def drag_on(zone: int) -> None:
            if zone == ZONES[-1]:
                dragged.append(low + step * len(dragged))
                await hass.services.async_call(
                    'number',
                    'set_value',
                    {'entity_id': bass, 'value': dragged[-1]},
                    blocking=True,
                )

        amp.after_read = drag_on
        await coordinator.async_refresh()
        amp.after_read = None
        await hass.async_block_till_done()

        assert len(dragged) == MAX_POLL_PASSES
        assert float(hass.states.get(bass).state) == dragged[-1]

        before = amp.zones[11]['bass']
        amp.zones[11]['bass'] = before + 2  # changed at the amplifier itself
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert float(hass.states.get(bass).state) != dragged[-1]

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_a_restore_and_an_import_count_as_commands(hass: HomeAssistant) -> None:
    """Changes sent outside the single-setting methods are seen by a running poll."""
    amp = _Amp()
    coordinator = XantechCoordinator(hass, amp, 'test_amp', ZONES)
    try:
        await coordinator.async_refresh()

        # an import: the settings were written through the network client
        amp.reads.clear()
        amp.stop_at = 12
        poll = hass.async_create_task(coordinator.async_refresh())
        await asyncio.wait_for(amp.stopped.wait(), 1)
        amp.zones[11]['volume'] = 3
        await coordinator.async_command_sent()
        amp.go_on.set()
        await poll
        assert coordinator.data[11]['volume'] == 3
        assert amp.reads == ZONES + ZONES

        # a restore: it waits for the running poll, which must not report the old state
        reports: list[int] = []
        coordinator.async_add_listener(
            lambda: reports.append(coordinator.data[11]['volume'])
        )
        amp.stopped.clear()
        amp.go_on.clear()
        amp.stop_at = 12
        poll = hass.async_create_task(coordinator.async_refresh())
        await asyncio.wait_for(amp.stopped.wait(), 1)
        restore = hass.async_create_task(
            coordinator.async_restore_zone(11, {'volume': 9})
        )
        while amp.zones[11]['volume'] != 9:
            await asyncio.sleep(0)
        amp.go_on.set()
        await poll
        await restore
        assert reports and set(reports) == {9}
    finally:
        await coordinator.async_shutdown()


# --- a command that fails; another zone's commands (found by an independent review) ---


@contextlib.asynccontextmanager
async def _running(hass: HomeAssistant) -> AsyncIterator[tuple[_Amp, MockConfigEntry]]:
    """The integration set up on a stand-in amplifier, zones 11 and 12 on."""
    amp, entry = _Amp(), _entry()
    amp.zones[11]['power'] = amp.zones[12]['power'] = True
    entry.add_to_hass(hass)
    with patch(
        'custom_components.xantech.async_get_amp_controller',
        AsyncMock(return_value=amp),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield amp, entry
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


def _player_id(hass: HomeAssistant, zone: int = 11) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        'media_player', DOMAIN, f'{DOMAIN}_{AMP}_zone_{zone}'
    )
    assert entity_id is not None
    return entity_id


def _volume(hass: HomeAssistant, entity_id: str) -> int:
    return round(hass.states.get(entity_id).attributes['volume_level'] * 38)


async def test_a_volume_step_that_failed_is_not_counted_by_the_next_one(
    hass: HomeAssistant,
) -> None:
    """From 10, a failed step up and then one more must ask for 11, not for 12."""
    async with _running(hass) as (amp, entry):
        player = _player_id(hass)
        coordinator = entry.runtime_data.coordinator
        asked: list[int] = []
        set_volume = amp.set_volume

        async def fails_once(zone: int, volume: int) -> None:
            asked.append(volume)
            if len(asked) == 1:
                raise OSError('no answer from the amplifier')
            await set_volume(zone, volume)

        amp.set_volume = fails_once  # type: ignore[method-assign]
        with (
            patch.object(
                coordinator,
                'async_request_refresh',
                wraps=coordinator.async_request_refresh,
            ) as refresh,
            pytest.raises(OSError, match='no answer'),
        ):
            await hass.services.async_call(
                'media_player', 'volume_up', {'entity_id': player}, blocking=True
            )
        assert _volume(hass, player) == 10  # not the 11 that never arrived
        await hass.async_block_till_done()
        refresh.assert_awaited()  # and the amplifier was asked what it has

        await hass.services.async_call(
            'media_player', 'volume_up', {'entity_id': player}, blocking=True
        )
        assert asked == [11, 11]
        assert (amp.zones[11]['volume'], _volume(hass, player)) == (11, 11)


async def test_a_cancelled_command_takes_back_what_it_showed(
    hass: HomeAssistant,
) -> None:
    async with _running(hass) as (amp, _entry_):
        player = _player_id(hass)
        entity = hass.data['media_player'].get_entity(player)
        sent, never = asyncio.Event(), asyncio.Event()

        async def hangs(_zone: int, _volume_: int) -> None:
            sent.set()
            await never.wait()

        amp.set_volume = hangs  # type: ignore[method-assign]
        task = hass.async_create_task(entity.async_volume_up())
        await asyncio.wait_for(sent.wait(), 1)
        assert _volume(hass, player) == 11  # shown at once
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _volume(hass, player) == 10
        await hass.async_block_till_done()


async def test_a_failed_command_leaves_a_later_one_on_show(hass: HomeAssistant) -> None:
    """The first of two commands fails while the second is on its way: 25 stays."""
    async with _running(hass) as (amp, _entry_):
        player = _player_id(hass)
        entity = hass.data['media_player'].get_entity(player)
        gates = [asyncio.Event(), asyncio.Event()]
        started: list[int] = []
        set_volume = amp.set_volume

        async def one_by_one(zone: int, volume: int) -> None:
            started.append(volume)
            mine = len(started) - 1
            await gates[mine].wait()
            if mine == 0:
                raise OSError('no answer from the amplifier')
            await set_volume(zone, volume)

        amp.set_volume = one_by_one  # type: ignore[method-assign]
        first = hass.async_create_task(entity.async_set_volume_level(20 / 38))
        second = hass.async_create_task(entity.async_set_volume_level(25 / 38))
        for _ in range(5):
            await asyncio.sleep(0)
        assert started == [20, 25]
        gates[0].set()
        with pytest.raises(OSError, match='no answer'):
            await first
        assert _volume(hass, player) == 25
        gates[1].set()
        await second
        await hass.async_block_till_done()
        assert (amp.zones[11]['volume'], _volume(hass, player)) == (25, 25)


async def test_a_failed_tone_value_is_not_left_on_show(hass: HomeAssistant) -> None:
    async with _running(hass) as (amp, _entry_):
        bass = er.async_get(hass).async_get_entity_id(
            'number', DOMAIN, f'{DOMAIN}_{AMP}_zone_11_bass'
        )
        assert bass is not None
        assert hass.states.get(bass).state == '-5.0'  # raw 7 on the amplifier

        async def fails(_zone: int, _value: int) -> None:
            raise OSError('no answer from the amplifier')

        amp.set_bass = fails  # type: ignore[method-assign]
        with pytest.raises(OSError, match='no answer'):
            await hass.services.async_call(
                'number', 'set_value', {'entity_id': bass, 'value': 3}, blocking=True
            )
        assert hass.states.get(bass).state == '-5.0'
        await hass.async_block_till_done()


async def test_commands_to_one_zone_do_not_hold_an_old_value_on_another(
    hass: HomeAssistant,
) -> None:
    """Zone 12 is changed in every pass. Zone 11 was read after its own last command,
    so it shows what the amplifier reported, not what it was set to before that.
    """
    async with _running(hass) as (amp, entry):
        coordinator = entry.runtime_data.coordinator
        eleven, twelve = _player_id(hass, 11), _player_id(hass, 12)
        steps: list[int] = []

        async def busy_on_twelve(zone: int) -> None:
            if zone != ZONES[-1]:
                return
            if not steps:
                # in the first pass zone 11 is set to 20, and then someone turns it
                # down to 4 at the amplifier itself
                await hass.services.async_call(
                    'media_player',
                    'volume_set',
                    {'entity_id': eleven, 'volume_level': 20 / 38},
                    blocking=True,
                )
                amp.zones[11]['volume'] = 4
            steps.append(25 + len(steps))
            await hass.services.async_call(
                'media_player',
                'volume_set',
                {'entity_id': twelve, 'volume_level': steps[-1] / 38},
                blocking=True,
            )

        amp.after_read = busy_on_twelve
        await coordinator.async_refresh()
        amp.after_read = None
        await hass.async_block_till_done()

        assert len(steps) == MAX_POLL_PASSES
        assert coordinator.overtaken
        assert coordinator.zone_overtaken(12) and not coordinator.zone_overtaken(11)
        # zone 12: its last value, although the last pass read the one before
        assert coordinator.data[12]['volume'] == steps[-2]
        assert _volume(hass, twelve) == steps[-1]
        # zone 11: read twice since it was set, and the amplifier says 4
        assert _volume(hass, eleven) == 4


async def test_a_restore_of_settings_counts_for_every_zone(hass: HomeAssistant) -> None:
    async with _running(hass) as (amp, entry):
        coordinator = entry.runtime_data.coordinator

        async def restore_in_every_pass(zone: int) -> None:
            if zone == ZONES[-1]:
                await coordinator.async_command_sent()  # no zone: may be any of them

        amp.after_read = restore_in_every_pass
        await coordinator.async_refresh()
        amp.after_read = None
        assert all(coordinator.zone_overtaken(zone) for zone in ZONES)
        await coordinator.async_refresh()
        assert not any(coordinator.zone_overtaken(zone) for zone in ZONES)
