"""Tests for the settings pages: names, standby, backup, restore, factory reset."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xantech.const import (
    CONF_AMP_TYPE,
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_FACTORY_RESET_ENABLED_UNTIL,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONF_SOURCES,
    CONF_TRANSPORT,
    CONF_ZONES,
    DOMAIN,
    TRANSPORT_NETWORK,
)
from custom_components.xantech.dax88_network import Dax88NetworkError

from .conftest import full_export

STATUS = {'power': False, 'volume': 4, 'mute': False, 'source': 1}


def _names() -> dict[str, Any]:
    return {
        'device': 'DAX 88_0000',
        'zones': {z: f'Room {z}' for z in range(1, 9)},
        'inputs': {s: f'Input{s}' for s in range(1, 8)},
    }


@pytest.fixture
def amp() -> Generator[MagicMock]:
    """A network client double; the integration is given it instead of a real one."""
    client = MagicMock()
    client.zone_status = AsyncMock(return_value=STATUS)
    client.names = AsyncMock(side_effect=lambda: _names())
    client.firmware = AsyncMock(return_value='6.5')
    client.set_names = AsyncMock(return_value=1)
    client.get_auto_standby = AsyncMock(return_value=120)
    client.set_auto_standby = AsyncMock()
    client.export_settings = AsyncMock(side_effect=lambda: full_export())
    client.import_settings = AsyncMock(return_value=[])
    client.factory_reset = AsyncMock()
    client.wait_for_restart = AsyncMock()
    client.close = MagicMock()
    with patch('custom_components.xantech.Dax88NetworkAmp', return_value=client):
        yield client


def _entry(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title='Xantech Multi-Zone Audio (DAX 88_0000)',
        data={
            CONF_TRANSPORT: TRANSPORT_NETWORK,
            CONF_HOST: '192.0.2.50',
            CONF_NETWORK_PORT: 8899,
            CONF_DEVICE_ID: 'TESTUUID',
            CONF_AMP_TYPE: 'dax88',
            CONF_ZONES: {11: {'name': 'Lounge'}, 12: {'name': 'Den'}},
            CONF_SOURCES: {1: {'name': 'TV'}, 8: {'name': 'Streaming'}},
        },
        options=options or {},
        unique_id=f'{DOMAIN}_dax88_TESTUUID',
    )
    entry.add_to_hass(hass)
    return entry


async def _loaded(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    entry = _entry(hass, options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _step(hass: HomeAssistant, entry: MockConfigEntry, *steps: str) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    for step in steps:
        result = await hass.config_entries.options.async_configure(
            result['flow_id'], {'next_step_id': step}
        )
    return result


async def _submit(hass: HomeAssistant, result: dict, data: dict[str, Any]) -> dict:
    return await hass.config_entries.options.async_configure(result['flow_id'], data)


async def _after_progress(hass: HomeAssistant, result: dict) -> dict:
    """Wait for a progress step's job and return the step that follows it."""
    for _ in range(10):
        if result['type'] not in (
            FlowResultType.SHOW_PROGRESS,
            FlowResultType.SHOW_PROGRESS_DONE,
        ):
            return result
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(result['flow_id'])
    raise AssertionError('the progress step never finished')


def _default(result: dict, field: str) -> Any:
    return next(k for k in result['data_schema'].schema if k == field).default()


def _all_names(**changes: str) -> dict[str, Any]:
    names = _names()
    data = {
        'device_name': names['device'],
        **{f'amp_zone_{z}': n for z, n in names['zones'].items()},
        **{f'amp_input_{i}': n for i, n in names['inputs'].items()},
    }
    return {**data, **changes}


# --- menu ---


async def test_menu_offers_the_amplifier_settings(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result['menu_options'][-5:] == [
        'amp_names',
        'standby',
        'backup',
        'factory_reset',
        'done',
    ]


async def test_settings_pages_say_when_the_amp_is_not_loaded(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = _entry(hass)  # never set up
    result = await _step(hass, entry, 'amp_names')
    assert (result['type'], result['reason']) == (FlowResultType.ABORT, 'not_loaded')
    result = await _step(hass, entry, 'standby')
    assert result['reason'] == 'not_loaded'
    result = await _step(hass, entry, 'backup', 'backup_now')
    assert result['reason'] == 'not_loaded'


# --- names stored on the amplifier ---


async def test_amp_names_form_shows_what_the_amp_has(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'amp_names')
    assert result['step_id'] == 'amp_names'
    assert _default(result, 'device_name') == 'DAX 88_0000'
    assert _default(result, 'amp_zone_8') == 'Room 8'
    assert _default(result, 'amp_input_7') == 'Input7'
    # 11 + 8 x 6 + 7 x 6 characters of the 132 the amplifier holds
    assert result['description_placeholders']['used'] == '101'
    assert result['description_placeholders']['limit'] == '132'


async def test_amp_names_are_checked_before_anything_is_sent(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'amp_names')
    result = await _submit(
        hass, result, _all_names(amp_zone_1='A' * 17, amp_input_2='Café')
    )
    assert result['errors'] == {
        'amp_zone_1': 'name_invalid',
        'amp_input_2': 'name_invalid',
    }
    assert _default(result, 'amp_zone_1') == 'A' * 17  # what was typed is kept
    # every name valid, but together more than the amplifier holds
    too_many = {f'amp_zone_{z}': 'Z' * 16 for z in range(1, 9)}
    result = await _submit(hass, result, _all_names(**too_many))
    assert result['errors'] == {'base': 'names_total'}
    amp.set_names.assert_not_called()


async def test_amp_names_are_written_and_can_be_used_in_ha(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'amp_names')
    result = await _submit(
        hass,
        result,
        {
            **_all_names(
                device_name='DAX 88_HOME',
                amp_zone_1='Living Room',
                amp_zone_2='Kitchen',
                amp_input_1='Roku',
            ),
            'use_in_ha': True,
        },
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    kwargs = amp.set_names.await_args.kwargs
    assert kwargs['device'] == 'DAX 88_HOME'
    assert kwargs['zones'][1] == 'Living Room'
    assert kwargs['zones'][8] == 'Room 8'
    assert kwargs['inputs'][1] == 'Roku'
    # Home Assistant's zones 11 and 12 are the amplifier's zones 1 and 2. Input 8 has
    # no name on the amplifier, so Home Assistant's own name for it is kept.
    assert entry.data[CONF_ZONES] == {
        11: {'name': 'Living Room'},
        12: {'name': 'Kitchen'},
    }
    assert entry.data[CONF_SOURCES] == {1: {'name': 'Roku'}, 8: {'name': 'Streaming'}}


async def test_amp_names_left_alone_in_ha_by_default(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'amp_names')
    result = await _submit(hass, result, _all_names(amp_zone_1='Living Room'))
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.data[CONF_ZONES][11] == {'name': 'Lounge'}


async def test_amp_names_reports_an_amp_that_stops_answering(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'amp_names')
    amp.set_names.side_effect = Dax88NetworkError('no reply')
    result = await _submit(hass, result, _all_names(amp_zone_1='Living Room'))
    assert result['errors'] == {'base': 'amp_stopped_answering'}


# --- auto-standby ---


async def test_standby_form_shows_the_current_time(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    amp.get_auto_standby.return_value = 270  # 4 h 30 min
    result = await _step(hass, entry, 'standby')
    assert _default(result, 'standby_enabled') is True
    assert _default(result, 'standby_hours') == 4
    assert _default(result, 'standby_minutes') == '30'


async def test_standby_is_set_and_read_back(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'standby')
    amp.get_auto_standby.side_effect = [120, 4770]  # before, then the read-back
    result = await _submit(
        hass,
        result,
        {'standby_enabled': True, 'standby_hours': 79, 'standby_minutes': '30'},
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    amp.set_auto_standby.assert_awaited_once_with(4770)


async def test_standby_can_be_switched_off(hass: HomeAssistant, amp: MagicMock) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'standby')
    amp.get_auto_standby.side_effect = [120, None]
    result = await _submit(
        hass,
        result,
        {'standby_enabled': False, 'standby_hours': 2, 'standby_minutes': '0'},
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    amp.set_auto_standby.assert_awaited_once_with(None)


async def test_standby_refuses_times_the_amp_cannot_hold(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'standby')
    result = await _submit(
        hass,
        result,
        {'standby_enabled': True, 'standby_hours': 0, 'standby_minutes': '0'},
    )
    assert result['errors'] == {'base': 'standby_range'}
    amp.set_auto_standby.assert_not_called()


async def test_standby_says_when_the_amp_did_not_keep_it(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'standby')
    result = await _submit(
        hass,
        result,
        {'standby_enabled': True, 'standby_hours': 4, 'standby_minutes': '0'},
    )  # the read-back still says 120
    assert result['errors'] == {'base': 'not_stored'}


# --- backup and restore ---


async def test_backup_now_writes_a_file(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'backup', 'backup_now')
    assert result['step_id'] == 'backup_done'
    name = result['description_placeholders']['backup_file']
    saved = json.loads((tmp_path / 'xantech_backups' / name).read_text())
    assert saved['format'] == 'dax88-network-settings/1'
    result = await _submit(hass, result, {})
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')


async def test_restore_needs_a_backup_to_exist(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'backup', 'restore_backup')
    assert (result['type'], result['reason']) == (FlowResultType.ABORT, 'no_backups')


async def test_restore_runs_with_a_progress_screen(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'backup', 'backup_now')
    name = result['description_placeholders']['backup_file']
    await _submit(hass, result, {})

    result = await _step(hass, entry, 'backup', 'restore_backup')
    assert _default(result, 'backup_file') == name  # newest first
    coordinator = entry.runtime_data.coordinator
    with patch.object(
        coordinator, 'async_command_sent', wraps=coordinator.async_command_sent
    ) as sent:
        result = await _submit(
            hass, result, {'backup_file': name, 'include_power': True}
        )
        result = await _after_progress(hass, result)
    assert result['step_id'] == 'restore_done'
    # a poll that was running during the restore must read again
    # (see test_command_during_poll)
    sent.assert_awaited_once()
    (backup,) = amp.import_settings.await_args.args
    assert backup['format'] == 'dax88-network-settings/1'
    assert amp.import_settings.await_args.kwargs == {'include_power': True}
    result = await _submit(hass, result, {})
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')


async def test_failed_restore_is_reported(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'backup', 'backup_now')
    name = result['description_placeholders']['backup_file']
    await _submit(hass, result, {})
    amp.import_settings.side_effect = Dax88NetworkError('no reply')

    result = await _step(hass, entry, 'backup', 'restore_backup')
    result = await _submit(hass, result, {'backup_file': name})
    result = await _after_progress(hass, result)
    # not "nothing was changed": part of the backup may already be on the amplifier
    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'restore_incomplete',
    )
    assert 'no reply' in result['description_placeholders']['detail']


# --- factory reset ---


async def test_reset_is_not_offered_while_the_switch_is_off(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'factory_reset')
    assert result['type'] == FlowResultType.MENU
    assert result['menu_options'] == ['factory_reset_switch', 'init']
    # the reset page cannot be opened: the menu does not accept it
    with pytest.raises(InvalidData):
        await _submit(hass, result, {'next_step_id': 'factory_reset_now'})
    amp.factory_reset.assert_not_called()


async def test_switch_then_typed_phrase_then_backup_then_reset(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)

    # 1. the kill switch
    result = await _step(hass, entry, 'factory_reset', 'factory_reset_switch')
    assert _default(result, 'allow_factory_reset') is False
    result = await _submit(hass, result, {'allow_factory_reset': True})
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'factory_reset')
    # the entry has reloaded with the switch on, and the reset is offered at once
    assert result['menu_options'] == [
        'factory_reset_switch',
        'factory_reset_now',
        'init',
    ]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options[CONF_FACTORY_RESET_ENABLED_UNTIL] > time.time()

    # 2. now the reset is offered, and it wants the phrase
    result = await _step(hass, entry, 'factory_reset')
    assert 'factory_reset_now' in result['menu_options']
    result = await _step(hass, entry, 'factory_reset', 'factory_reset_now')
    assert result['description_placeholders'] == {'phrase': 'FACTORY RESET'}
    result = await _submit(hass, result, {'confirm': 'factory reset'})
    assert result['errors'] == {'confirm': 'confirm_mismatch'}
    amp.factory_reset.assert_not_called()
    amp.export_settings.assert_not_called()

    # 3. the phrase: a backup is saved, then the reset runs behind a progress screen
    result = await _submit(hass, result, {'confirm': 'FACTORY RESET'})
    result = await _after_progress(hass, result)
    assert result['step_id'] == 'factory_reset_done'
    name = result['description_placeholders']['backup_file']
    saved = json.loads((tmp_path / 'xantech_backups' / name).read_text())
    phrase, backup = amp.factory_reset.await_args.args
    assert phrase == 'FACTORY RESET'
    assert backup == saved
    result = await _submit(hass, result, {})
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    await hass.async_block_till_done()

    # one reset per opt-in: the switch is off again
    assert CONF_FACTORY_RESET_ENABLED_UNTIL not in entry.options
    result = await _step(hass, entry, 'factory_reset')
    assert result['menu_options'] == ['factory_reset_switch', 'init']


async def test_no_reset_when_the_backup_fails(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, {CONF_FACTORY_RESET_ENABLED_UNTIL: time.time() + 600})
    amp.export_settings.side_effect = Dax88NetworkError('no reply')
    result = await _step(hass, entry, 'factory_reset', 'factory_reset_now')
    result = await _submit(hass, result, {'confirm': 'FACTORY RESET'})
    result = await _after_progress(hass, result)
    assert (result['type'], result['reason']) == (FlowResultType.ABORT, 'amp_no_answer')
    amp.factory_reset.assert_not_called()
    # the switch stays as it was: nothing was reset
    assert CONF_FACTORY_RESET_ENABLED_UNTIL in entry.options


# --- what the device page shows ---


async def test_firmware_is_shown_on_the_device(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert (DOMAIN, 'dax88_net_TESTUUID') in device.identifiers
    assert device.sw_version == '6.5'


async def test_connection_sensor_for_a_single_link(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        'sensor', DOMAIN, f'{DOMAIN}_dax88_net_testuuid_connection'
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state.state == 'network'
    assert state.attributes['connection_mode'] == 'network'
    assert state.attributes['amplifier_answering'] is True
    assert registry.async_get(entity_id).entity_category == 'diagnostic'
    assert entry.runtime_data.mode == 'network'


async def test_connection_sensor_follows_the_active_link(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title='Xantech Multi-Zone Audio (/dev/ttyUSB0)',
        data={
            CONF_PORT: '/dev/ttyUSB0',
            CONF_HOST: '192.0.2.50',
            CONF_CONNECTION_MODE: 'serial_fallback_network',
            CONF_AMP_TYPE: 'dax88',
            CONF_ZONES: {11: {'name': 'Lounge'}},
            CONF_SOURCES: {1: {'name': 'TV'}},
        },
        unique_id=f'{DOMAIN}_/dev/ttyUSB0',
    )
    entry.add_to_hass(hass)
    serial = MagicMock()
    serial.zone_status = AsyncMock(return_value=STATUS)
    serial.close = MagicMock()
    with patch(
        'custom_components.xantech.async_get_amp_controller',
        AsyncMock(return_value=serial),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = er.async_get(hass).async_get_entity_id(
            'sensor', DOMAIN, f'{DOMAIN}_dax88__dev_ttyusb0_connection'
        )
        assert hass.states.get(entity_id).state == 'serial'

        # the cable stops answering: two missed polls move to the network module
        serial.zone_status = AsyncMock(return_value=None)
        coordinator = entry.runtime_data.coordinator
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == 'network'
        # zones kept answering throughout, from the other link
        assert coordinator.last_update_success


async def test_result_page_shows_even_when_the_job_is_instant(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    """A job that finishes at once must not make the result page close by itself."""
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'backup', 'backup_now')
    name = result['description_placeholders']['backup_file']
    await _submit(hass, result, {})

    result = await _step(hass, entry, 'backup', 'restore_backup')
    with patch('custom_components.xantech.settings.async_import', AsyncMock()) as job:
        result = await _submit(hass, result, {'backup_file': name})
        assert result['type'] == FlowResultType.SHOW_PROGRESS
        result = await _after_progress(hass, result)
    job.assert_awaited_once()
    # power was not included, so the page says so (see test_restore_safety)
    assert (result['type'], result['step_id']) == (
        FlowResultType.FORM,
        'restore_done_no_power',
    )
    assert result['description_placeholders'] == {'backup_file': name, 'zones': ''}


# --- a page goes back to the menu; the dialog closes only on "Done" ---


async def test_several_pages_in_one_visit(hass: HomeAssistant, amp: MagicMock) -> None:
    """Asked for after the live test: the dialog used to close after every page."""
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'standby')
    amp.get_auto_standby.side_effect = [120, 150]
    result = await _submit(
        hass,
        result,
        {'standby_enabled': True, 'standby_hours': 2, 'standby_minutes': '30'},
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')

    # the same dialog: another page, which saves and reloads the entry
    async def slow_status(_zone: int) -> dict[str, Any]:
        await asyncio.sleep(0.05)  # a real amplifier takes a moment to answer
        return STATUS

    amp.zone_status = AsyncMock(side_effect=slow_status)
    result = await _submit(hass, result, {'next_step_id': 'polling'})
    result = await _submit(hass, result, {'scan_interval': 60})
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.options['scan_interval'] == 60
    # the reload is over before the menu is shown: the amplifier pages work at once
    assert entry.state is ConfigEntryState.LOADED
    result = await _submit(hass, result, {'next_step_id': 'amp_names'})
    assert (result['type'], result['step_id']) == (FlowResultType.FORM, 'amp_names')
    result = await _submit(hass, result, _all_names())
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')

    # "Done" closes the dialog and leaves the options as the pages saved them
    result = await _submit(hass, result, {'next_step_id': 'done'})
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['data'] == {'scan_interval': 60}


async def test_a_page_submitted_unchanged_changes_nothing(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    """The way back to the menu when a page was opened by mistake."""
    entry = await _loaded(hass)
    before = (dict(entry.data), dict(entry.options))
    with patch.object(hass.config_entries, 'async_reload') as reload:
        result = await _step(hass, entry, 'zones')
        result = await _submit(hass, result, {'zone_ids': ['11', '12']})
        result = await _submit(hass, result, {'zone_11': 'Lounge', 'zone_12': 'Den'})
        assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
        result = await _submit(hass, result, {'next_step_id': 'sources'})
        sources = {'source_1': 'TV', 'source_8': 'Streaming'}
        result = await _submit(hass, result, sources)
        result = await _submit(hass, result, {'next_step_id': 'standby'})
        result = await _submit(
            hass,
            result,
            {'standby_enabled': True, 'standby_hours': 2, 'standby_minutes': '0'},
        )
        result = await _submit(hass, result, {'next_step_id': 'amp_names'})
        result = await _submit(hass, result, _all_names())
        await hass.async_block_till_done()
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert (dict(entry.data), dict(entry.options)) == before
    reload.assert_not_called()
    amp.set_auto_standby.assert_not_called()


async def test_names_are_read_again_on_the_next_visit(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    """What one page read from the amplifier is not shown later as if still current."""
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'amp_names')
    assert _default(result, 'amp_zone_1') == 'Room 1'
    result = await _submit(hass, result, _all_names(amp_zone_1='Living Room'))
    renamed = _names()
    renamed['zones'][1] = 'Living Room'
    amp.names.side_effect = lambda: renamed
    result = await _submit(hass, result, {'next_step_id': 'amp_names'})
    assert _default(result, 'amp_zone_1') == 'Living Room'


# --- zones are shown as numbered on the amplifier, with their name ---


def _zone_labels(result: dict) -> dict[str, str]:
    field = result['data_schema'].schema['zone_ids']
    return {option['value']: option['label'] for option in field.config['options']}


async def test_zones_page_shows_number_and_name(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    """Asked for after the live test: "Zone 11" said little about which room it is."""
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'zones')
    labels = _zone_labels(result)
    # a zone in use: the name Home Assistant has for it
    assert labels['11'] == 'Zone 1 (Lounge)'
    assert labels['12'] == 'Zone 2 (Den)'
    # a zone not in use: the name stored on the amplifier
    assert labels['17'] == 'Zone 7 (Room 7)'
    assert list(labels) == [str(z) for z in range(11, 19)]


async def test_zones_page_without_an_answer_still_opens(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    amp.names.side_effect = Dax88NetworkError('no reply')
    result = await _step(hass, entry, 'zones')
    labels = _zone_labels(result)
    assert labels['11'] == 'Zone 1 (Lounge)'
    assert labels['17'] == 'Zone 7'


async def test_sub_menus_have_a_way_back(hass: HomeAssistant, amp: MagicMock) -> None:
    """The dialog has no back button, so each sub-menu offers one itself."""
    entry = await _loaded(hass)
    for sub_menu in ('backup', 'factory_reset'):
        result = await _step(hass, entry, sub_menu)
        assert result['menu_options'][-1] == 'init'
        result = await _submit(hass, result, {'next_step_id': 'init'})
        assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')


async def test_result_pages_lead_on_with_next(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'backup', 'backup_now')
    assert result['step_id'] == 'backup_done'
    assert result['last_step'] is False


async def test_unchanged_pages_do_not_reload_after_a_restart(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    """Seen on a real system: stored numbers are text, and looked like a change."""
    entry = _entry(hass)
    stored = json.loads(json.dumps(dict(entry.data)))  # as read back from disk
    hass.config_entries.async_update_entry(entry, data=stored)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert list(entry.data[CONF_ZONES]) == ['11', '12']
    before = dict(entry.data)
    with patch.object(hass.config_entries, 'async_reload') as reload:
        result = await _step(hass, entry, 'zones')
        result = await _submit(hass, result, {'zone_ids': ['11', '12']})
        result = await _submit(hass, result, {'zone_11': 'Lounge', 'zone_12': 'Den'})
        result = await _submit(hass, result, {'next_step_id': 'sources'})
        sources = {'source_1': 'TV', 'source_8': 'Streaming'}
        result = await _submit(hass, result, sources)
        await hass.async_block_till_done()
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    reload.assert_not_called()
    assert dict(entry.data) == before
