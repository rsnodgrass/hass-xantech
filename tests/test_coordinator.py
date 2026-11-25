"""Tests for Xantech coordinator."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.xantech.audio_controls import (
    SNAPSHOT_SETTINGS,
    restorable_settings,
)
from custom_components.xantech.coordinator import XantechCoordinator


@pytest.fixture
async def coordinator(
    hass: HomeAssistant, mock_amp: MagicMock
) -> AsyncGenerator[XantechCoordinator]:
    """Create a coordinator for testing."""
    coord = XantechCoordinator(
        hass=hass,
        amp=mock_amp,
        amp_name='test_amp',
        zone_ids=[11, 12, 13],
        scan_interval=30,
    )
    yield coord
    # cancel the refresh debouncer so no timer outlives the test
    await coord.async_shutdown()


async def test_coordinator_update_success(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test successful coordinator data update."""
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data is not None
    assert 11 in coordinator.data
    assert coordinator.data[11]['power'] is True
    assert coordinator.data[11]['volume'] == 20


async def test_coordinator_update_partial_failure(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test coordinator handles partial zone failures gracefully."""
    # make zone 12 fail
    call_count = 0

    async def zone_status_with_failure(zone_id: int) -> dict | None:
        nonlocal call_count
        call_count += 1
        if zone_id == 12:
            raise Exception('Zone 12 failed')
        return {
            'power': True,
            'volume': 20,
            'mute': False,
            'source': 1,
        }

    mock_amp.zone_status = AsyncMock(side_effect=zone_status_with_failure)

    await coordinator.async_refresh()

    # should still succeed overall, just missing zone 12
    assert coordinator.last_update_success
    assert 11 in coordinator.data
    assert 13 in coordinator.data
    assert 12 not in coordinator.data


async def test_coordinator_set_zone_power(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test setting zone power through coordinator."""
    await coordinator.async_set_zone_power(11, True)

    mock_amp.set_power.assert_called_once_with(11, True)


async def test_coordinator_set_zone_volume(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test setting zone volume through coordinator."""
    await coordinator.async_set_zone_volume(11, 25)

    mock_amp.set_volume.assert_called_once_with(11, 25)


async def test_coordinator_set_zone_mute(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test setting zone mute through coordinator."""
    await coordinator.async_set_zone_mute(11, True)

    mock_amp.set_mute.assert_called_once_with(11, True)


async def test_coordinator_set_zone_source(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test setting zone source through coordinator."""
    await coordinator.async_set_zone_source(11, 2)

    mock_amp.set_source.assert_called_once_with(11, 2)


async def test_coordinator_snapshot_zone(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test getting zone snapshot."""
    snapshot = await coordinator.async_get_zone_snapshot(11)

    mock_amp.zone_status.assert_called_with(11)
    assert snapshot is not None
    assert snapshot['power'] is True


async def test_coordinator_restore_zone(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Restore sends each saved setting with the ordinary set commands.

    The snapshot's own zone number (1 here) is ignored: the zone id Home Assistant
    uses (11) is what the amplifier is addressed with.
    """
    snapshot = {
        'zone': 1,
        'power': True,
        'volume': 30,
        'mute': False,
        'source': 2,
        'bass': 15,
        'treble': None,  # not reported by this amp: skipped
    }
    await coordinator.async_restore_zone(11, snapshot)

    mock_amp.set_source.assert_awaited_once_with(11, 2)
    mock_amp.set_volume.assert_awaited_once_with(11, 30)
    mock_amp.set_mute.assert_awaited_once_with(11, False)
    mock_amp.set_bass.assert_awaited_once_with(11, 15)
    mock_amp.set_treble.assert_not_called()
    mock_amp.restore_zone.assert_not_called()  # the library's restore is not relied on
    sent = [c[0] for c in mock_amp.method_calls if c[0].startswith('set_')]
    assert sent[-1] == 'set_power'  # switched on last, after volume and input


async def test_coordinator_restore_switches_off_first(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    await coordinator.async_restore_zone(12, {'power': False, 'volume': 10})

    sent = [c[0] for c in mock_amp.method_calls if c[0].startswith('set_')]
    assert sent == ['set_power', 'set_volume']
    mock_amp.set_power.assert_awaited_once_with(12, False)


async def test_coordinator_set_power_error_handling(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test error handling when setting zone power fails."""
    mock_amp.set_power.side_effect = Exception('Connection lost')

    with pytest.raises(Exception, match='Connection lost'):
        await coordinator.async_set_zone_power(11, True)


# --- a snapshot only holds what the amplifier reports ---

FULL_SNAPSHOT = {
    'zone': 1,
    'power': True,
    'mute': False,
    'volume': 0,
    'treble': 0,
    'bass': 0,
    'balance': 0,
    'source': 0,
}


async def test_restore_leaves_out_what_the_amp_never_reported(
    hass: HomeAssistant, mock_amp: MagicMock
) -> None:
    """The serial library fills in 0 for settings an amp's status does not contain.

    Restoring those would set a real volume or input to 0, or send a command the amp
    does not have.
    """
    coord = XantechCoordinator(
        hass, mock_amp, 'test_amp', [11], restorable=frozenset({'volume', 'mute'})
    )
    try:
        await coord.async_restore_zone(11, FULL_SNAPSHOT)
    finally:
        await coord.async_shutdown()
    sent = [c[0] for c in mock_amp.method_calls if c[0].startswith('set_')]
    assert sent == ['set_volume', 'set_mute', 'set_power']


async def test_a_sonance_zone_gets_its_input_and_volume_back(
    hass: HomeAssistant, mock_amp: MagicMock
) -> None:
    """Only power was restored: the snapshot's input and volume were dropped."""
    coord = XantechCoordinator(
        hass, mock_amp, 'test_amp', [1], restorable=restorable_settings('sonance6')
    )
    snapshot = {'zone': 1, 'power': True, 'source': 3, 'volume': 25, 'mute': False}
    try:
        await coord.async_restore_zone(1, snapshot)
    finally:
        await coord.async_shutdown()
    mock_amp.set_source.assert_awaited_once_with(1, 3)
    mock_amp.set_volume.assert_awaited_once_with(1, 25)
    mock_amp.set_mute.assert_awaited_once_with(1, False)
    # a setting whose question the amplifier did not answer is not in the snapshot
    mock_amp.set_bass.assert_not_awaited()


def test_what_each_amp_type_can_restore() -> None:
    assert restorable_settings('dax88') == SNAPSHOT_SETTINGS
    assert restorable_settings('xantech8') == SNAPSHOT_SETTINGS
    assert restorable_settings('monoprice6') == SNAPSHOT_SETTINGS
    # no single status reply: the library asks for each setting and all of them count
    assert restorable_settings('sonance6') == SNAPSHOT_SETTINGS
    # reports balance, but has no command to set it
    assert 'balance' not in restorable_settings('zpr68-10')
    assert {'source', 'volume', 'mute'} <= restorable_settings('zpr68-10')
    # an amp type the library does not describe: nothing is known, so nothing is cut
    assert restorable_settings('not_a_real_amp') == SNAPSHOT_SETTINGS


async def test_a_restore_shows_the_restored_state_at_once(
    hass: HomeAssistant, mock_amp: MagicMock
) -> None:
    """Seen on a real system: after a restore the zone kept its old values for a while.

    A restore usually follows other commands, and the ordinary refresh is held back
    for ten seconds after the last one.
    """
    coord = XantechCoordinator(hass, mock_amp, 'test_amp', [11])
    try:
        await coord.async_request_refresh()  # a command just before the restore
        before = dict(coord.data[11])
        mock_amp.zone_status = AsyncMock(return_value={**before, 'volume': 7})
        await coord.async_restore_zone(11, {'volume': 7})
        assert coord.data[11]['volume'] == 7
    finally:
        await coord.async_shutdown()
