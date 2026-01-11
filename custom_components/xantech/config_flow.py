"""Config flow for Xantech Multi-Zone Amplifier integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from pyxantech import async_get_amp_controller
from serial import SerialException
import voluptuous as vol

from .const import (
    CONF_AMP_TYPE,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SOURCES,
    CONF_ZONES,
    DEFAULT_AMP_TYPE,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SUPPORTED_AMP_TYPES,
)

LOG = logging.getLogger(__name__)


class XantechConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xantech Multi-Zone Amplifier."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial step - basic connection setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            port = user_input[CONF_PORT]
            amp_type = user_input[CONF_AMP_TYPE]

            # test connection to amplifier
            try:
                amp = await async_get_amp_controller(amp_type, port, self.hass.loop)
                if amp:
                    # try to verify communication works
                    # we'll just store the config for now
                    await self.async_set_unique_id(f'{DOMAIN}_{port}')
                    self._abort_if_unique_id_configured()

                    self._data = user_input
                    return await self.async_step_zones()

            except SerialException:
                LOG.error('Serial connection error to %s', port)
                errors['base'] = 'cannot_connect'
            except Exception:
                LOG.exception('Unexpected error during connection test')
                errors['base'] = 'unknown'

        return self.async_show_form(
            step_id='user',
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORT, default='/dev/ttyUSB0'): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(
                        CONF_AMP_TYPE, default=DEFAULT_AMP_TYPE
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=SUPPORTED_AMP_TYPES,
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key='amp_type',
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_zones(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle zone configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # parse zones configuration
            zones_text = user_input.get('zones_config', '')
            zones = self._parse_zones_config(zones_text)

            if not zones:
                errors['base'] = 'invalid_zones'
            else:
                self._data[CONF_ZONES] = zones
                return await self.async_step_sources()

        # default zone config for the amp type
        default_zones = self._get_default_zones_text(
            self._data.get(CONF_AMP_TYPE, DEFAULT_AMP_TYPE)
        )

        return self.async_show_form(
            step_id='zones',
            data_schema=vol.Schema(
                {
                    vol.Required('zones_config', default=default_zones): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.TEXT,
                            multiline=True,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                'example': '11: Living Room\n12: Kitchen\n13: Bedroom',
            },
        )

    async def async_step_sources(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle source configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # parse sources configuration
            sources_text = user_input.get('sources_config', '')
            sources = self._parse_sources_config(sources_text)

            if not sources:
                errors['base'] = 'invalid_sources'
            else:
                self._data[CONF_SOURCES] = sources
                return self.async_create_entry(
                    title=f'{DEFAULT_NAME} ({self._data[CONF_PORT]})',
                    data=self._data,
                )

        default_sources = '1: TV\n2: Streaming\n3: Turntable\n4: CD Player'

        return self.async_show_form(
            step_id='sources',
            data_schema=vol.Schema(
                {
                    vol.Required(
                        'sources_config', default=default_sources
                    ): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.TEXT,
                            multiline=True,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                'example': '1: Sonos\n2: Turntable\n3: TV',
            },
        )

    def _parse_zones_config(self, text: str) -> dict[int, dict[str, str]]:
        """Parse zone configuration text into structured dict.

        Format: 'zone_id: zone_name' per line
        """
        zones: dict[int, dict[str, str]] = {}
        for line in text.strip().split('\n'):
            line = line.strip()
            if not line or ':' not in line:
                continue
            try:
                zone_id_str, name = line.split(':', 1)
                zone_id = int(zone_id_str.strip())
                zones[zone_id] = {'name': name.strip()}
            except ValueError:
                continue
        return zones

    def _parse_sources_config(self, text: str) -> dict[int, dict[str, str]]:
        """Parse source configuration text into structured dict.

        Format: 'source_id: source_name' per line
        """
        sources: dict[int, dict[str, str]] = {}
        for line in text.strip().split('\n'):
            line = line.strip()
            if not line or ':' not in line:
                continue
            try:
                source_id_str, name = line.split(':', 1)
                source_id = int(source_id_str.strip())
                sources[source_id] = {'name': name.strip()}
            except ValueError:
                continue
        return sources

    def _get_default_zones_text(self, amp_type: str) -> str:
        """Get default zones text for an amplifier type."""
        if amp_type == 'monoprice6':
            return (
                '11: Living Room\n12: Kitchen\n13: Master Bedroom\n'
                '14: Office\n15: Patio\n16: Dining Room'
            )
        if amp_type == 'dax88':
            return (
                '11: Living Room\n12: Kitchen\n13: Master Bedroom\n'
                '14: Office\n15: Patio\n16: Dining Room\n17: Garage\n18: Basement'
            )
        # default to xantech8
        return (
            '11: Living Room\n12: Kitchen\n13: Master Bedroom\n'
            '14: Office\n15: Patio\n16: Dining Room'
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return XantechOptionsFlow(config_entry)


class XantechOptionsFlow(OptionsFlow):
    """Handle options flow for Xantech integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title='', data=user_input)

        current_scan_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        return self.async_show_form(
            step_id='init',
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=current_scan_interval,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=5,
                            max=300,
                            step=5,
                            mode=NumberSelectorMode.SLIDER,
                            unit_of_measurement='seconds',
                        )
                    ),
                }
            ),
        )
