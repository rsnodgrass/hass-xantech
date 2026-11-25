"""Tests for Xantech config flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from serialx import SerialException

from custom_components.xantech.config_flow import (
    default_zone_ids,
    flat_zone_numbers,
    read_sources,
    read_zone_ids,
    read_zone_names,
    valid_zone_ids,
    zone_label,
)
from custom_components.xantech.const import (
    CONF_AMP_TYPE,
    CONF_ENABLE_AUDIO_CONTROLS,
    CONF_FACTORY_RESET_ENABLED_UNTIL,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SOURCES,
    CONF_ZONES,
    DOMAIN,
)


async def _start_serial(hass: HomeAssistant) -> dict:
    """Open the flow and choose Serial (RS-232) from the first menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )
    assert result['type'] == FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result['flow_id'], {'next_step_id': 'serial'}
    )


def _fields(result: dict) -> list[str]:
    return [str(key) for key in result['data_schema'].schema]


def _default(result: dict, field: str) -> Any:
    key = next(k for k in result['data_schema'].schema if k == field)
    return key.default()


def _suggested(result: dict, field: str) -> Any:
    key = next(k for k in result['data_schema'].schema if k == field)
    return (key.description or {}).get('suggested_value')


async def test_user_step_offers_serial_or_network(hass: HomeAssistant) -> None:
    """The first step asks how the amp is connected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )

    assert result['type'] == FlowResultType.MENU
    assert result['step_id'] == 'user'
    assert result['menu_options'] == ['serial', 'network']


async def test_serial_form_shows(hass: HomeAssistant) -> None:
    """Choosing serial shows the serial port form."""
    result = await _start_serial(hass)
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'serial'
    assert result['errors'] == {}


async def test_user_form_connection_error(
    hass: HomeAssistant,
) -> None:
    """Test handling of connection error during config flow."""
    with patch(
        'custom_components.xantech.config_flow.async_get_amp_controller',
        side_effect=SerialException('Connection failed'),
    ):
        result = await _start_serial(hass)

        result = await hass.config_entries.flow.async_configure(
            result['flow_id'],
            {
                CONF_PORT: '/dev/ttyUSB0',
                CONF_AMP_TYPE: 'xantech8',
            },
        )

    assert result['type'] == FlowResultType.FORM
    assert result['errors'] == {'base': 'cannot_connect'}


async def test_user_form_unknown_error(
    hass: HomeAssistant,
) -> None:
    """Test handling of unknown error during config flow."""
    with patch(
        'custom_components.xantech.config_flow.async_get_amp_controller',
        side_effect=Exception('Unknown error'),
    ):
        result = await _start_serial(hass)

        result = await hass.config_entries.flow.async_configure(
            result['flow_id'],
            {
                CONF_PORT: '/dev/ttyUSB0',
                CONF_AMP_TYPE: 'xantech8',
            },
        )

    assert result['type'] == FlowResultType.FORM
    assert result['errors'] == {'base': 'unknown'}


async def _to_zones(hass: HomeAssistant, amp_type: str = 'xantech8') -> dict:
    result = await _start_serial(hass)
    return await hass.config_entries.flow.async_configure(
        result['flow_id'], {CONF_PORT: '/dev/ttyUSB0', CONF_AMP_TYPE: amp_type}
    )


async def test_full_flow_success(
    hass: HomeAssistant,
    mock_async_get_amp_controller: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """Every setting is a field: tick zones, name them, name the inputs."""
    result = await _to_zones(hass)

    # zones: a tick list, with the first controller's zones ticked
    assert result['step_id'] == 'zones'
    assert _default(result, 'zone_ids') == ['11', '12', '13', '14', '15', '16']
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {'zone_ids': ['12', '11'], CONF_ENABLE_AUDIO_CONTROLS: True},
    )

    # zone names: one field per ticked zone
    assert result['step_id'] == 'zone_names'
    assert _fields(result) == ['zone_11', 'zone_12']
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'zone_11': ' Living Room ', 'zone_12': 'Kitchen'}
    )

    # inputs: one optional field each
    assert result['step_id'] == 'sources'
    assert _fields(result) == [f'source_{n}' for n in range(1, 9)]
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'source_1': 'Sonos', 'source_3': 'TV'}
    )

    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['title'] == 'Xantech Multi-Zone Audio (/dev/ttyUSB0)'
    data = result['data']
    assert data[CONF_PORT] == '/dev/ttyUSB0'
    assert data[CONF_AMP_TYPE] == 'xantech8'
    assert data[CONF_ZONES] == {11: {'name': 'Living Room'}, 12: {'name': 'Kitchen'}}
    assert data[CONF_SOURCES] == {1: {'name': 'Sonos'}, 3: {'name': 'TV'}}
    assert data[CONF_ENABLE_AUDIO_CONTROLS] is True


async def test_zones_step_needs_a_zone(
    hass: HomeAssistant,
    mock_async_get_amp_controller: AsyncMock,
) -> None:
    result = await _to_zones(hass)
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'zone_ids': []}
    )
    assert result['step_id'] == 'zones'
    assert result['errors'] == {'base': 'invalid_zones'}


async def test_sources_step_needs_an_input(
    hass: HomeAssistant,
    mock_async_get_amp_controller: AsyncMock,
) -> None:
    result = await _to_zones(hass)
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'zone_ids': ['11']}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'zone_11': 'Living Room'}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'source_2': '   '}
    )
    assert result['step_id'] == 'sources'
    assert result['errors'] == {'base': 'invalid_sources'}


def test_options_flow_stores_config_entry_correctly() -> None:
    """Test that XantechOptionsFlow stores config_entry in the correct attribute.

    The HA OptionsFlow base class has a config_entry property that reads from
    _config_entry, so the flow must store the entry there.
    """
    from custom_components.xantech.config_flow import XantechOptionsFlow

    mock_entry = MagicMock()
    mock_entry.data = {'port': '/dev/ttyUSB0', 'amp_type': 'xantech8'}
    mock_entry.options = {}

    flow = XantechOptionsFlow(mock_entry)

    assert hasattr(flow, '_config_entry'), 'Flow must store entry in _config_entry'
    assert flow._config_entry is mock_entry
    assert flow._config_entry.data['port'] == '/dev/ttyUSB0'


@pytest.mark.parametrize(
    ('amp_type', 'low', 'high'),
    [('xantech8', 11, 18), ('monoprice6', 11, 16), ('dax88', 11, 18)],
)
def test_default_zones_use_double_digit(amp_type: str, low: int, high: int) -> None:
    """Most models address the first amplifier's zones as 11-18."""
    defaults = default_zone_ids(amp_type)
    assert defaults
    assert all(low <= z <= high for z in defaults)


def test_default_zones_are_valid_for_every_amp_type() -> None:
    """Sonance and ZPR68 number zones from 1: the ticked defaults must be valid."""
    for amp_type in ('xantech8', 'monoprice6', 'dax88', 'zpr68-10', 'sonance6'):
        valid = set(valid_zone_ids(amp_type))
        assert set(default_zone_ids(amp_type)) <= valid, amp_type
    assert default_zone_ids('sonance6') == [1, 2, 3, 4, 5, 6]


def test_zone_and_source_readers_ignore_what_the_amp_cannot_do() -> None:
    assert read_zone_ids('dax88', {'zone_ids': ['18', '11', '99']}) == [11, 18]
    assert read_sources('sonance6', {'source_1': 'TV', 'source_7': 'Ghost'}) == {
        1: {'name': 'TV'}
    }


# --- options: zones, inputs, polling ---


def _entry(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title='Xantech Multi-Zone Audio (/dev/ttyUSB0)',
        data={
            CONF_PORT: '/dev/ttyUSB0',
            CONF_AMP_TYPE: 'dax88',
            # stored keys become strings after a restart
            CONF_ZONES: {'11': {'name': 'Living Room'}, '12': {'name': 'Kitchen'}},
            CONF_SOURCES: {'1': {'name': 'Sonos'}, '5': {'name': 'TV'}},
        },
        options=options or {},
        unique_id=f'{DOMAIN}_/dev/ttyUSB0',
    )
    entry.add_to_hass(hass)
    return entry


async def _options_step(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result['type'] == FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result['flow_id'], {'next_step_id': step}
    )


async def test_options_menu_for_a_serial_amp(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result['menu_options'] == [
        'connection',
        'zones',
        'sources',
        'features',
        'polling',
        'done',
    ]


async def test_options_zones_add_remove_and_rename(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    result = await _options_step(hass, entry, 'zones')
    assert _default(result, 'zone_ids') == ['11', '12']
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], {'zone_ids': ['11', '13']}
    )
    assert result['step_id'] == 'zone_names'
    assert _fields(result) == ['zone_11', 'zone_13']
    assert _default(result, 'zone_11') == 'Living Room'  # existing name kept
    assert _default(result, 'zone_13') == 'Zone 3'  # as numbered on the amplifier
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], {'zone_11': 'Lounge', 'zone_13': 'Office'}
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.data[CONF_ZONES] == {11: {'name': 'Lounge'}, 13: {'name': 'Office'}}


async def test_options_sources_edit_and_hide(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    result = await _options_step(hass, entry, 'sources')
    assert _suggested(result, 'source_1') == 'Sonos'
    assert _suggested(result, 'source_2') == ''
    # input 1 renamed, input 5 left empty (hidden), input 8 added
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], {'source_1': 'Streamer', 'source_8': 'Wi-Fi'}
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.data[CONF_SOURCES] == {1: {'name': 'Streamer'}, 8: {'name': 'Wi-Fi'}}


async def test_polling_keeps_the_other_options(hass: HomeAssistant) -> None:
    """Changing the polling interval must not wipe unrelated options."""
    entry = _entry(hass, {CONF_FACTORY_RESET_ENABLED_UNTIL: 9999999999.0})
    result = await _options_step(hass, entry, 'polling')
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], {CONF_SCAN_INTERVAL: 60}
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert dict(entry.options) == {
        CONF_FACTORY_RESET_ENABLED_UNTIL: 9999999999.0,
        CONF_SCAN_INTERVAL: 60,
    }


# --- zone labels ---


@pytest.mark.parametrize(
    ('zone_id', 'name', 'label'),
    [
        (11, None, 'Zone 1'),
        (18, None, 'Zone 8'),
        (11, 'Lounge', 'Zone 1 (Lounge)'),
        (23, None, 'Amplifier 2, zone 3'),
        (36, 'Pool', 'Amplifier 3, zone 6 (Pool)'),
        (5, None, 'Zone 5'),  # models that number their zones 1-8
        (5, 'Patio', 'Zone 5 (Patio)'),
        (10, None, 'Zone 10'),
    ],
)
def test_zone_label(zone_id: int, name: str | None, label: str) -> None:
    assert zone_label(zone_id, name) == label


def _amp_types() -> list[str]:
    from pyxantech.config import DEVICE_CONFIG

    return sorted(DEVICE_CONFIG)


@pytest.mark.parametrize('amp_type', _amp_types())
def test_no_two_zones_of_a_model_have_the_same_label(amp_type: str) -> None:
    """A ZPR68 numbers its zones 1 to 15: zone 11 was shown as "Zone 1", like zone 1."""
    labels = [zone_label(z, None, amp_type) for z in valid_zone_ids(amp_type)]
    assert len(set(labels)) == len(labels), labels


@pytest.mark.parametrize(
    ('amp_type', 'zone_id', 'label'),
    [
        ('zpr68-10', 1, 'Zone 1'),
        ('zpr68-10', 11, 'Zone 11'),
        ('zpr68-10', 15, 'Zone 15'),
        ('xantech8', 3, 'Zone 3'),
        ('xantech8', 23, 'Zone 23'),  # the model also has a zone 3: the id as it is
        ('sonance6', 6, 'Zone 6'),
        ('dax88', 11, 'Zone 1'),
        ('monoprice6', 23, 'Amplifier 2, zone 3'),
        ('dax66', 36, 'Amplifier 3, zone 6'),
        ('not_a_real_amp', 11, 'Zone 1'),
    ],
)
def test_zone_label_follows_how_the_model_numbers_its_zones(
    amp_type: str, zone_id: int, label: str
) -> None:
    assert zone_label(zone_id, None, amp_type) == label


def test_which_models_number_their_zones_from_one() -> None:
    assert flat_zone_numbers('zpr68-10') and flat_zone_numbers('xantech8')
    assert not flat_zone_numbers('dax88') and not flat_zone_numbers('monoprice6')
    assert not flat_zone_numbers(None)


def test_the_default_name_of_a_zone_follows_the_model_too() -> None:
    assert read_zone_names([11], {'zone_11': ''}, 'zpr68-10') == {
        11: {'name': 'Zone 11'}
    }
    assert read_zone_names([11], {'zone_11': ''}, 'dax88') == {11: {'name': 'Zone 1'}}


def test_a_zone_left_without_a_name_gets_its_label() -> None:
    assert read_zone_names([11, 23], {'zone_11': ' ', 'zone_23': 'Pool'}) == {
        11: {'name': 'Zone 1'},
        23: {'name': 'Pool'},
    }


async def test_options_zones_page_labels_for_a_serial_amp(hass: HomeAssistant) -> None:
    """Over RS-232 only Home Assistant's own names are known."""
    entry = _entry(hass)
    result = await _options_step(hass, entry, 'zones')
    field = result['data_schema'].schema['zone_ids']
    labels = {o['value']: o['label'] for o in field.config['options']}
    assert labels['11'] == 'Zone 1 (Living Room)'
    assert labels['12'] == 'Zone 2 (Kitchen)'
    assert labels['13'] == 'Zone 3'
    assert result['last_step'] is False  # the names page follows


async def test_done_closes_the_dialog(hass: HomeAssistant) -> None:
    entry = _entry(hass, {CONF_SCAN_INTERVAL: 45})
    result = await _options_step(hass, entry, 'done')
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['data'] == {CONF_SCAN_INTERVAL: 45}
