"""A factory reset or restore is never mis-reported, let past a guard or left half-done.

These cover what happens around the amplifier commands: what the user is told when the
amplifier does not confirm, what is (not) sent when a guard changes in the meantime,
and that a job the user stopped watching still finishes and reports.
"""
# ruff: noqa: F811 - the amp fixture is shared with test_amp_settings

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import timedelta
import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.xantech import settings
from custom_components.xantech.const import CONF_FACTORY_RESET_ENABLED_UNTIL, DOMAIN
from custom_components.xantech.dax88_network import (
    Dax88NetworkError,
    FactoryResetUnconfirmed,
)

from .conftest import full_export
from .test_amp_settings import (  # noqa: F401 - amp is a fixture
    _after_progress,
    _loaded,
    _step,
    _submit,
    amp,
)

SWITCH_NOTICE = 'Amplifier factory reset is enabled'
PHRASE = 'FACTORY RESET'


def _switch_on(seconds: float = 600) -> dict[str, Any]:
    return {CONF_FACTORY_RESET_ENABLED_UNTIL: time.time() + seconds}


@pytest.fixture
def notices() -> Generator[list[tuple[str, str]]]:
    """(title, message) of every notification the settings module shows."""
    shown: list[tuple[str, str]] = []

    def create(_hass: Any, message: str, title: str = '', **_kwargs: Any) -> None:
        shown.append((title, message))

    with patch(
        'custom_components.xantech.settings.persistent_notification.async_create',
        side_effect=create,
    ):
        yield shown


def _results(notices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Notifications about something that happened (not the "switch is on" notice)."""
    return [n for n in notices if n[0] != SWITCH_NOTICE]


async def _start_reset(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    result = await _step(hass, entry, 'factory_reset', 'factory_reset_now')
    return await _submit(hass, result, {'confirm': PHRASE})


async def _reset_action(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    return await hass.services.async_call(
        DOMAIN,
        'factory_reset',
        {'config_entry_id': entry.entry_id, 'confirm': PHRASE},
        blocking=True,
        return_response=True,
    )


async def _until(condition: Any) -> None:
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError('never happened')


# --- what the user is told ---


async def test_reset_the_amp_did_not_confirm_is_reported_as_sent(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any, notices: list
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, _switch_on())
    amp.wait_for_restart.side_effect = FactoryResetUnconfirmed(
        '192.0.2.50 did not come back after the factory reset'
    )
    result = await _after_progress(hass, await _start_reset(hass, entry))

    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'reset_unconfirmed',
    )
    name = result['description_placeholders']['backup_file']
    assert (tmp_path / 'xantech_backups' / name).is_file()
    amp.factory_reset.assert_awaited_once()
    # never "nothing was changed": a notification names the backup, the switch is off
    ((title, message),) = _results(notices)
    assert 'not confirmed' in title
    assert name in message and 'Treat it as reset' in message
    await hass.async_block_till_done()
    assert CONF_FACTORY_RESET_ENABLED_UNTIL not in entry.options


async def test_reset_that_could_not_be_sent_changes_nothing(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any, notices: list
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, _switch_on())
    amp.factory_reset.side_effect = Dax88NetworkError('cannot connect to 192.0.2.50')
    result = await _after_progress(hass, await _start_reset(hass, entry))

    assert (result['type'], result['reason']) == (FlowResultType.ABORT, 'amp_no_answer')
    amp.wait_for_restart.assert_not_called()
    assert _results(notices) == []  # nothing claims a reset happened
    assert CONF_FACTORY_RESET_ENABLED_UNTIL in entry.options  # still the user's call


@pytest.mark.parametrize('refused_by', ['integration', 'client'])
async def test_no_reset_when_the_backup_could_not_be_put_back(
    hass: HomeAssistant,
    amp: MagicMock,
    tmp_path: Any,
    notices: list,
    refused_by: str,
) -> None:
    """The amp holds a name that cannot be written back: a reset would lose it."""
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, _switch_on())
    if refused_by == 'integration':
        names = {str(z): f'Room {z}' for z in range(1, 9)} | {'3': 'Caf�'}
        amp.export_settings.side_effect = lambda: full_export(zone_names=names)
    else:
        amp.factory_reset.side_effect = ValueError(
            'the backup could not be put back afterwards (zone 3 name: ...)'
        )
    result = await _after_progress(hass, await _start_reset(hass, entry))

    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'backup_unusable',
    )
    assert 'zone 3 name' in result['description_placeholders']['detail']
    if refused_by == 'integration':
        amp.factory_reset.assert_not_called()
    amp.wait_for_restart.assert_not_called()
    assert _results(notices) == []
    assert CONF_FACTORY_RESET_ENABLED_UNTIL in entry.options


# --- guards that change while the reset is being prepared ---


async def test_switch_turned_off_during_the_backup_stops_the_reset(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, _switch_on())

    async def export_while_the_user_turns_it_off() -> dict[str, Any]:
        hass.config_entries.async_update_entry(entry, options={})
        return full_export()

    amp.export_settings.side_effect = export_while_the_user_turns_it_off
    with pytest.raises(ServiceValidationError):
        await _reset_action(hass, entry)
    amp.factory_reset.assert_not_called()
    assert not settings.reset_running(entry)
    await hass.async_block_till_done()


async def test_reload_during_the_backup_stops_the_reset(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    """The client the reset started with is no longer the entry's client."""
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, _switch_on())

    async def export_while_the_entry_reloads() -> dict[str, Any]:
        entry.runtime_data.network_amp = MagicMock()  # what a reload leaves behind
        return full_export()

    amp.export_settings.side_effect = export_while_the_entry_reloads
    with pytest.raises(ServiceValidationError, match='reloaded'):
        await _reset_action(hass, entry)
    amp.factory_reset.assert_not_called()


async def test_closing_the_dialog_does_not_stop_a_reset_that_was_sent(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any, notices: list
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, _switch_on())
    back = asyncio.Event()
    amp.wait_for_restart.side_effect = back.wait

    result = await _start_reset(hass, entry)
    assert result['type'] == FlowResultType.SHOW_PROGRESS
    try:
        await _until(lambda: amp.wait_for_restart.await_count)
        assert settings.reset_running(entry)

        # a second reset is refused while this one is waiting for the amplifier
        with pytest.raises(ServiceValidationError, match='already running'):
            await asyncio.wait_for(_reset_action(hass, entry), 2)
        assert amp.factory_reset.await_count == 1

        # the window is closed
        hass.config_entries.options.async_abort(result['flow_id'])
        await asyncio.sleep(0.02)
        assert settings.reset_running(entry)  # still waiting, not cancelled
        assert _results(notices) == []
    finally:
        back.set()
    await hass.async_block_till_done()
    ((title, message),) = _results(notices)
    assert title == 'Amplifier factory reset done'
    (saved,) = (tmp_path / 'xantech_backups').iterdir()
    assert saved.name in message
    assert not settings.reset_running(entry)
    assert CONF_FACTORY_RESET_ENABLED_UNTIL not in entry.options


# --- the switch's own timer ---


async def test_the_switch_turns_itself_off_when_its_time_is_up(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass, _switch_on(60))
    assert CONF_FACTORY_RESET_ENABLED_UNTIL in entry.options
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert CONF_FACTORY_RESET_ENABLED_UNTIL not in entry.options


async def test_the_timer_does_not_reload_the_entry_under_a_running_reset(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass, _switch_on(60))
    settings._RESETTING.add(entry.entry_id)
    try:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
        await hass.async_block_till_done()
        assert CONF_FACTORY_RESET_ENABLED_UNTIL in entry.options
    finally:
        settings._RESETTING.discard(entry.entry_id)
    # it looks again a little later, once the reset has ended
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=100))
    await hass.async_block_till_done()
    assert CONF_FACTORY_RESET_ENABLED_UNTIL not in entry.options


# --- restore ---


async def _backup(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    result = await _step(hass, entry, 'backup', 'backup_now')
    await _submit(hass, result, {})
    return result['description_placeholders']['backup_file']


async def test_closing_the_dialog_does_not_stop_a_restore(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any, notices: list
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    name = await _backup(hass, entry)
    go_on = asyncio.Event()
    finished: list[bool] = []

    async def slow_restore(*_args: Any, **_kwargs: Any) -> None:
        await go_on.wait()
        finished.append(True)

    amp.import_settings.side_effect = slow_restore
    result = await _step(hass, entry, 'backup', 'restore_backup')
    result = await _submit(hass, result, {'backup_file': name})
    assert result['type'] == FlowResultType.SHOW_PROGRESS
    await _until(lambda: amp.import_settings.await_count)

    hass.config_entries.options.async_abort(result['flow_id'])  # the window is closed
    await asyncio.sleep(0.02)
    go_on.set()
    await hass.async_block_till_done()
    assert finished == [True]  # the restore ran to its end
    ((title, message),) = _results(notices)
    assert title == 'Amplifier restore finished' and name in message


async def test_restore_of_a_file_with_a_bad_value_sends_nothing(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    bad = full_export()
    bad['zones']['8']['volume'] = 99
    (tmp_path / 'xantech_backups').mkdir()
    (tmp_path / 'xantech_backups' / 'edited.json').write_text(json.dumps(bad))
    with pytest.raises(ServiceValidationError, match='zone 8'):
        await hass.services.async_call(
            DOMAIN,
            'import_settings',
            {'config_entry_id': entry.entry_id, 'backup_file': 'edited.json'},
            blocking=True,
        )
    amp.import_settings.assert_not_called()


# --- backup files ---


async def test_a_backup_never_replaces_an_earlier_one(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    """Two backups in the same second (same file name stem) are two files."""
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    real = time.strftime

    def same_second(fmt: str, *args: Any) -> str:
        return '20260101-000000' if fmt == '%Y%m%d-%H%M%S' else real(fmt, *args)

    with patch('custom_components.xantech.settings.time.strftime', same_second):
        first = await settings.async_export(hass, entry)
        amp.export_settings.side_effect = lambda: full_export(device_name='Second')
        second = await settings.async_export(hass, entry)
    assert first != second
    folder = tmp_path / 'xantech_backups'
    assert json.loads((folder / first).read_text())['device_name'] == 'DAX 88_0000'
    assert json.loads((folder / second).read_text())['device_name'] == 'Second'


async def test_a_backup_file_name_has_no_runs_of_dashes(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    hass.config_entries.async_update_entry(
        entry, title='Xantech Multi-Zone Audio (/dev/ttyUSB0)'
    )
    with patch(
        'custom_components.xantech.settings.time.strftime',
        lambda *_: '20260101-000000',
    ):
        name = await settings.async_export(hass, entry)
    assert name == 'xantech-multi-zone-audio-dev-ttyusb0-20260101-000000.json'
    hass.config_entries.async_update_entry(entry, title='(***)')
    with patch(
        'custom_components.xantech.settings.time.strftime',
        lambda *_: '20260101-000000',
    ):
        assert await settings.async_export(hass, entry) == (
            'amplifier-20260101-000000.json'
        )


async def test_backup_that_cannot_be_saved_says_so(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    (tmp_path / 'xantech_backups').write_text('a file where the folder should be')
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'backup', 'backup_now')
    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'backup_not_saved',
    )
    assert result['description_placeholders']['detail']
