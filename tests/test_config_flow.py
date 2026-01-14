"""Tests for Xantech config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from serial import SerialException

from custom_components.xantech.const import (
    CONF_AMP_TYPE,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)


async def test_user_form_shows_on_init(hass: HomeAssistant) -> None:
    """Test that the user form is shown on initialization."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )

    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'user'
    assert result['errors'] == {}


async def test_user_form_connection_error(
    hass: HomeAssistant,
) -> None:
    """Test handling of connection error during config flow."""
    with patch(
        'custom_components.xantech.config_flow.async_get_amp_controller',
        side_effect=SerialException('Connection failed'),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': config_entries.SOURCE_USER}
        )

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
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result['flow_id'],
            {
                CONF_PORT: '/dev/ttyUSB0',
                CONF_AMP_TYPE: 'xantech8',
            },
        )

    assert result['type'] == FlowResultType.FORM
    assert result['errors'] == {'base': 'unknown'}


async def test_full_flow_success(
    hass: HomeAssistant,
    mock_async_get_amp_controller: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test successful completion of full config flow."""
    # step 1: user - connection info
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'user'

    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {
            CONF_PORT: '/dev/ttyUSB0',
            CONF_AMP_TYPE: 'xantech8',
        },
    )

    # step 2: zones
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'zones'

    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {
            'zones_config': '11: Living Room\n12: Kitchen',
        },
    )

    # step 3: sources
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'sources'

    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {
            'sources_config': '1: Sonos\n2: TV',
        },
    )

    # final: entry created
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['title'] == 'Xantech Multi-Zone Audio (/dev/ttyUSB0)'
    assert result['data'][CONF_PORT] == '/dev/ttyUSB0'
    assert result['data'][CONF_AMP_TYPE] == 'xantech8'


async def test_zones_step_invalid_config(
    hass: HomeAssistant,
    mock_async_get_amp_controller: AsyncMock,
) -> None:
    """Test zones step with invalid configuration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {
            CONF_PORT: '/dev/ttyUSB0',
            CONF_AMP_TYPE: 'xantech8',
        },
    )

    # submit empty zones config
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {
            'zones_config': '',
        },
    )

    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'zones'
    assert result['errors'] == {'base': 'invalid_zones'}


async def test_sources_step_invalid_config(
    hass: HomeAssistant,
    mock_async_get_amp_controller: AsyncMock,
) -> None:
    """Test sources step with invalid configuration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {
            CONF_PORT: '/dev/ttyUSB0',
            CONF_AMP_TYPE: 'xantech8',
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {
            'zones_config': '11: Living Room',
        },
    )

    # submit empty sources config
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {
            'sources_config': '',
        },
    )

    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'sources'
    assert result['errors'] == {'base': 'invalid_sources'}


def test_options_flow_stores_config_entry_correctly() -> None:
    """Test that XantechOptionsFlow stores config_entry in the correct attribute.

    This test verifies that the OptionsFlow properly stores the config_entry
    using the correct internal attribute name (_config_entry) so that the
    parent class property works correctly. The HA OptionsFlow base class has
    a config_entry property that reads from _config_entry.

    If __init__ used self.config_entry = config_entry instead of
    self._config_entry = config_entry, the options flow would fail at runtime
    because the parent class property would return None.
    """
    from unittest.mock import MagicMock

    from custom_components.xantech.config_flow import XantechOptionsFlow

    mock_entry = MagicMock()
    mock_entry.data = {'port': '/dev/ttyUSB0', 'amp_type': 'xantech8'}
    mock_entry.options = {}

    flow = XantechOptionsFlow(mock_entry)

    # verify the config_entry is stored in _config_entry (not config_entry)
    # the parent OptionsFlow class has a config_entry property that reads _config_entry
    assert hasattr(flow, '_config_entry'), 'Flow must store entry in _config_entry'
    assert flow._config_entry is mock_entry
    assert flow._config_entry.data['port'] == '/dev/ttyUSB0'
