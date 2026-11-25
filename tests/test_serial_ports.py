"""Tests for serial port handling (serialx, ESPHome serial proxies)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import SerialPortSelector
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from serialx import SerialException

from custom_components.xantech.const import (
    CONF_AMP_NAME,
    CONF_AMP_TYPE,
    CONF_FALLBACK_PORT,
    CONF_HOST,
    CONF_PORT,
    CONF_SOURCES,
    CONF_ZONES,
    DOMAIN,
)
from custom_components.xantech.diagnostics import _redact_port
from custom_components.xantech.port import port_label

ESPHOME_HASS_URL = 'esphome-hass://esphome/0123abcd?port_name=RS-232+Port+1'


def _schema_validator(schema: Any, key: str) -> Any:
    """Return the validator HA registered for a form field."""
    for marker, validator in schema.schema.items():
        if str(marker) == key:
            return validator
    raise AssertionError(f'{key} not in schema')


async def _start_serial(hass: HomeAssistant) -> dict:
    """Open the flow and choose Serial (RS-232) from the first menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result['flow_id'], {'next_step_id': 'serial'}
    )


async def test_user_form_offers_serial_port_selector(hass: HomeAssistant) -> None:
    """The port field is HA's serial port picker, so ESPHome proxies are listed."""
    result = await _start_serial(hass)

    validator = _schema_validator(result['data_schema'], CONF_PORT)
    assert isinstance(validator, SerialPortSelector)


@pytest.mark.parametrize(
    'port',
    ['/dev/ttyUSB0', 'socket://192.168.1.10:4999/', ESPHOME_HASS_URL],
)
async def test_selector_accepts_every_port_form(port: str) -> None:
    """Device paths, socket URLs and serial proxy URLs all validate."""
    assert SerialPortSelector()(port) == port


@pytest.mark.parametrize(
    'error',
    [SerialException('boom'), OSError('no such device'), TimeoutError()],
)
async def test_user_form_connection_errors_map_to_cannot_connect(
    hass: HomeAssistant, error: Exception
) -> None:
    """serialx raises OSError/TimeoutError/SerialException, not pyserial's."""
    with patch(
        'custom_components.xantech.config_flow.async_get_amp_controller',
        side_effect=error,
    ):
        result = await _start_serial(hass)
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'],
            {CONF_PORT: ESPHOME_HASS_URL, CONF_AMP_TYPE: 'xantech8'},
        )

    assert result['type'] == FlowResultType.FORM
    assert result['errors'] == {'base': 'cannot_connect'}


async def test_full_flow_with_esphome_serial_proxy(
    hass: HomeAssistant,
    mock_async_get_amp_controller: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """The proxy URL is stored as-is and the title shows the port name only."""
    result = await _start_serial(hass)
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {CONF_PORT: ESPHOME_HASS_URL, CONF_AMP_TYPE: 'xantech8'},
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'zone_ids': ['11']}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'zone_11': 'Living Room'}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'source_1': 'Sonos'}
    )

    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['data'][CONF_PORT] == ESPHOME_HASS_URL
    assert result['title'] == 'Xantech Multi-Zone Audio (RS-232 Port 1)'
    mock_async_get_amp_controller.assert_awaited_once()
    assert mock_async_get_amp_controller.await_args.args[1] == ESPHOME_HASS_URL


@pytest.mark.parametrize(
    'error', [SerialException('boom'), OSError('gone'), TimeoutError()]
)
async def test_setup_retries_when_port_unavailable(
    hass: HomeAssistant, config_entry: Any, error: Exception
) -> None:
    """A proxy that is offline at startup must retry, not fail permanently."""
    with patch('custom_components.xantech.async_get_amp_controller', side_effect=error):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


class TestPortLabel:
    """Human-readable port names for titles and logs."""

    def test_serial_proxy_shows_port_name(self) -> None:
        assert port_label(ESPHOME_HASS_URL) == 'RS-232 Port 1'

    @pytest.mark.parametrize('port', ['/dev/ttyUSB0', 'socket://host:4999/'])
    def test_other_ports_unchanged(self, port: str) -> None:
        assert port_label(port) == port

    def test_credentials_never_appear(self) -> None:
        url = 'esphome://192.0.2.5:6053/?port_name=RS-232+Port+1&key=SECRET'
        assert 'SECRET' not in port_label(url)


def test_diagnostics_redacts_esphome_credentials() -> None:
    url = 'esphome://192.0.2.5:6053/?port_name=RS-232+Port+1&key=SECRET'
    assert 'SECRET' not in _redact_port(url)


# --- this fork's additions on top of the serial port picker ---


async def test_connection_page_uses_the_port_picker_for_both_rs232_links(
    hass: HomeAssistant, config_entry: Any
) -> None:
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], {'next_step_id': 'connection'}
    )
    schema = result['data_schema']
    assert isinstance(_schema_validator(schema, CONF_PORT), SerialPortSelector)
    assert isinstance(_schema_validator(schema, CONF_FALLBACK_PORT), SerialPortSelector)
    assert not isinstance(_schema_validator(schema, CONF_HOST), SerialPortSelector)
    # the port in use is shown; a link that is not set has no value at all
    suggested = {
        str(key): (key.description or {}).get('suggested_value')
        for key in schema.schema
    }
    assert suggested[CONF_PORT] == config_entry.data[CONF_PORT]
    assert suggested[CONF_FALLBACK_PORT] is None
    assert suggested[CONF_HOST] is None


async def test_a_serial_proxy_url_stays_out_of_entity_ids(
    hass: HomeAssistant, mock_amp: Any
) -> None:
    """Entity unique ids are built from the port's label, as upstream does."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title='Xantech Multi-Zone Audio (RS-232 Port 1)',
        data={
            CONF_PORT: ESPHOME_HASS_URL,
            CONF_AMP_TYPE: 'xantech8',
            CONF_ZONES: {11: {'name': 'Living Room'}},
            CONF_SOURCES: {1: {'name': 'Sonos'}},
        },
        unique_id=f'{DOMAIN}_{ESPHOME_HASS_URL}',
    )
    entry.add_to_hass(hass)
    with patch(
        'custom_components.xantech.async_get_amp_controller',
        AsyncMock(return_value=mock_amp),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.data[CONF_AMP_NAME] == 'xantech8_RS-232 Port 1'
    states = [s.entity_id for s in hass.states.async_all('media_player')]
    assert states == ['media_player.xantech_xantech8_rs_232_port_1_living_room']
    await hass.config_entries.async_unload(entry.entry_id)
