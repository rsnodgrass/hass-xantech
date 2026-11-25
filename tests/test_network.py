"""Tests for the DAX88 network transport: setup flow, kill switch and services."""

from __future__ import annotations

from collections.abc import Generator
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.xantech.const import (
    CONF_AMP_TYPE,
    CONF_DEVICE_ID,
    CONF_FACTORY_RESET_ENABLED_UNTIL,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_SOURCES,
    CONF_TRANSPORT,
    CONF_ZONES,
    DOMAIN,
    TRANSPORT_NETWORK,
)
from custom_components.xantech.network import NotADax88Error

from .conftest import full_export

MODULE = {
    'uuid': 'TESTUUID',
    'project': 'DAYTONAUDIO_DAX88',
    'DeviceName': 'DAX 88_0000',
}
NAMES = {
    'device': 'DAX 88_0000',
    'zones': {z: f'Room {z}' for z in range(1, 9)},
    'inputs': {s: f'Input{s}' for s in range(1, 8)},
}


def _network_amp() -> MagicMock:
    amp = MagicMock()
    amp.zone_status = AsyncMock(
        return_value={'power': False, 'volume': 4, 'mute': False, 'source': 1}
    )
    amp.names = AsyncMock(return_value=NAMES)
    amp.firmware = AsyncMock(return_value='6.5')
    amp.set_names = AsyncMock(return_value=1)
    amp.get_auto_standby = AsyncMock(return_value=120)
    amp.set_auto_standby = AsyncMock()
    amp.export_settings = AsyncMock(side_effect=lambda: full_export())
    amp.import_settings = AsyncMock()
    amp.factory_reset = AsyncMock()
    amp.wait_for_restart = AsyncMock()
    amp.close = MagicMock()
    return amp


@pytest.fixture
def network_amp() -> Generator[MagicMock]:
    amp = _network_amp()
    with (
        patch('custom_components.xantech.Dax88NetworkAmp', return_value=amp) as cls,
        patch(
            'custom_components.xantech.config_flow.Dax88NetworkAmp', return_value=amp
        ),
    ):
        amp.cls = cls
        yield amp


# --- config flow ---


async def test_network_flow_uses_the_amps_own_names(
    hass: HomeAssistant, network_amp: MagicMock
) -> None:
    with (
        patch(
            'custom_components.xantech.config_flow.async_check_linkplay_amp',
            AsyncMock(return_value=MODULE),
        ),
        patch('custom_components.xantech.async_setup_entry', return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {'next_step_id': 'network'}
        )
        assert result['step_id'] == 'network'
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {CONF_HOST: ' 192.0.2.50 '}
        )
        assert result['step_id'] == 'zones'

        def key(field: str) -> Any:
            return next(k for k in result['data_schema'].schema if k == field)

        assert key('zone_ids').default() == [str(z) for z in range(11, 19)]
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {'zone_ids': ['11', '18']}
        )
        # zone names are pre-filled from the amplifier (its zone 1 is zone 11 here)
        assert result['step_id'] == 'zone_names'
        assert key('zone_11').default() == 'Room 1'
        assert key('zone_18').default() == 'Room 8'
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {'zone_11': 'Room 1', 'zone_18': 'Room 8'}
        )
        # and so are the inputs; input 8 is the amplifier's streaming input
        assert result['step_id'] == 'sources'
        assert key('source_1').description == {'suggested_value': 'Input1'}
        assert key('source_8').description == {'suggested_value': 'Streaming'}
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {'source_1': 'Input1', 'source_8': 'Streaming'}
        )
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['title'] == 'Xantech Multi-Zone Audio (DAX 88_0000)'
    data = result['data']
    assert data[CONF_TRANSPORT] == TRANSPORT_NETWORK
    assert data[CONF_HOST] == '192.0.2.50'
    assert data[CONF_DEVICE_ID] == 'TESTUUID'
    assert data[CONF_AMP_TYPE] == 'dax88'
    assert data['device_label'] == 'DAX 88_0000'  # the device name in HA
    assert data[CONF_ZONES] == {11: {'name': 'Room 1'}, 18: {'name': 'Room 8'}}
    assert data[CONF_SOURCES] == {1: {'name': 'Input1'}, 8: {'name': 'Streaming'}}
    assert result['result'].unique_id == f'{DOMAIN}_dax88_TESTUUID'
    network_amp.close.assert_called()  # the test connection is released


async def test_network_flow_refuses_other_devices(hass: HomeAssistant) -> None:
    with patch(
        'custom_components.xantech.config_flow.async_check_linkplay_amp',
        AsyncMock(side_effect=NotADax88Error('OTHER_SPEAKER')),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {'next_step_id': 'network'}
        )
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {CONF_HOST: '192.0.2.9'}
        )
    assert result['errors'] == {'base': 'not_dax88'}


async def test_network_flow_unreachable(hass: HomeAssistant) -> None:
    with patch(
        'custom_components.xantech.config_flow.async_check_linkplay_amp',
        AsyncMock(side_effect=TimeoutError),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {'next_step_id': 'network'}
        )
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {CONF_HOST: '192.0.2.9'}
        )
    assert result['errors'] == {'base': 'cannot_connect_network'}


# --- a loaded network entry ---


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
            CONF_ZONES: {11: {'name': 'Room 1'}, 12: {'name': 'Room 2'}},
            CONF_SOURCES: {1: {'name': 'Input1'}},
        },
        options=options or {},
        unique_id=f'{DOMAIN}_dax88_TESTUUID',
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_uses_network_client_and_stable_ids(
    hass: HomeAssistant, network_amp: MagicMock
) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    args, kwargs = network_amp.cls.call_args
    assert args == ('192.0.2.50', 8899)
    assert kwargs['allow_factory_reset'] is False  # kill switch off by default
    assert entry.runtime_data.coordinator.amp_name == 'dax88_net_TESTUUID'


async def _reset(hass: HomeAssistant, entry: MockConfigEntry, **data: Any) -> Any:
    return await hass.services.async_call(
        DOMAIN,
        'factory_reset',
        {'config_entry_id': entry.entry_id, **data},
        blocking=True,
        return_response=True,
    )


async def test_factory_reset_refused_while_switched_off(
    hass: HomeAssistant, network_amp: MagicMock
) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    with pytest.raises(ServiceValidationError, match='switched off'):
        await _reset(hass, entry, confirm='FACTORY RESET')
    network_amp.factory_reset.assert_not_called()
    network_amp.export_settings.assert_not_called()


async def test_factory_reset_guards_and_backup(
    hass: HomeAssistant, network_amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = _entry(hass, {CONF_FACTORY_RESET_ENABLED_UNTIL: time.time() + 600})
    with (
        patch(
            'custom_components.xantech.settings.persistent_notification.async_create'
        ) as shown,
        patch(
            'custom_components.xantech.settings.persistent_notification.async_dismiss'
        ) as gone,
    ):
        await _setup(hass, entry)
        # while the switch is on, a notice says so
        assert any(
            'Factory reset is enabled' in c.args[1] for c in shown.call_args_list
        )
        assert network_amp.cls.call_args.kwargs['allow_factory_reset'] is True
        await _run_guarded_reset(hass, entry, network_amp, tmp_path)
        # turned off after the reset, and the notice is removed
        gone.assert_called()


async def _run_guarded_reset(
    hass: HomeAssistant, entry: MockConfigEntry, network_amp: MagicMock, tmp_path: Any
) -> None:
    with pytest.raises(ServiceValidationError, match='FACTORY RESET'):
        await _reset(hass, entry, confirm='factory reset')
    # there is no way to skip the backup, or to pass some other file as the backup
    for extra in ({'create_backup': False}, {'backup_file': 'other.json'}):
        with pytest.raises(vol.Invalid):
            await _reset(hass, entry, confirm='FACTORY RESET', **extra)
    network_amp.factory_reset.assert_not_called()

    response = await _reset(hass, entry, confirm='FACTORY RESET')
    backup_name = response['backup_file']
    saved = json.loads((tmp_path / 'xantech_backups' / backup_name).read_text())
    assert saved['format'] == 'dax88-network-settings/1'
    network_amp.factory_reset.assert_awaited_once()
    phrase, backup = network_amp.factory_reset.await_args.args
    assert phrase == 'FACTORY RESET'
    assert backup == saved
    network_amp.wait_for_restart.assert_awaited_once()
    await hass.async_block_till_done()
    # one reset per opt-in: the kill switch is off again
    assert CONF_FACTORY_RESET_ENABLED_UNTIL not in entry.options


async def test_kill_switch_expires_by_itself(
    hass: HomeAssistant, network_amp: MagicMock
) -> None:
    entry = _entry(hass, {CONF_FACTORY_RESET_ENABLED_UNTIL: time.time() - 1})
    await _setup(hass, entry)
    await hass.async_block_till_done()
    assert CONF_FACTORY_RESET_ENABLED_UNTIL not in entry.options


async def test_import_only_reads_from_the_backup_folder(
    hass: HomeAssistant, network_amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = _entry(hass)
    await _setup(hass, entry)
    # files that hold a perfectly valid backup, but are not backups in the folder:
    # one outside it, and one inside it that is not a .json file
    valid = json.dumps(full_export())
    (tmp_path / 'outside.json').write_text(valid)
    (tmp_path / 'xantech_backups').mkdir()
    (tmp_path / 'xantech_backups' / 'notes.txt').write_text(valid)
    for bad in ('../outside.json', str(tmp_path / 'outside.json'), 'notes.txt'):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                'import_settings',
                {'config_entry_id': entry.entry_id, 'backup_file': bad},
                blocking=True,
            )
    network_amp.import_settings.assert_not_called()
    response = await hass.services.async_call(
        DOMAIN,
        'export_settings',
        {'config_entry_id': entry.entry_id},
        blocking=True,
        return_response=True,
    )
    await hass.services.async_call(
        DOMAIN,
        'import_settings',
        {'config_entry_id': entry.entry_id, 'backup_file': response['backup_file']},
        blocking=True,
    )
    network_amp.import_settings.assert_awaited_once()


async def test_services_refuse_serial_entries(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_amp: MagicMock
) -> None:
    with patch(
        'custom_components.xantech.async_get_amp_controller',
        AsyncMock(return_value=mock_amp),
    ):
        await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError, match='network'):
        await _reset(hass, config_entry, confirm='FACTORY RESET')
