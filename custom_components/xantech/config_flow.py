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
    BooleanSelector,
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
    CONF_ENABLE_AUDIO_CONTROLS,
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
                            mode=SelectSelectorMode.LIST,
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
                self._data[CONF_ENABLE_AUDIO_CONTROLS] = user_input.get(
                    CONF_ENABLE_AUDIO_CONTROLS, False
                )
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
                    vol.Optional(
                        CONF_ENABLE_AUDIO_CONTROLS, default=False
                    ): BooleanSelector(),
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

        default_sources = self._get_default_sources_text(
            self._data.get(CONF_AMP_TYPE, DEFAULT_AMP_TYPE)
        )

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

    def _get_default_sources_text(self, amp_type: str) -> str:
        """Get default sources text for an amplifier type."""
        # 8-source amps: xantech8, dax88
        if amp_type in ('xantech8', 'dax88'):
            return (
                '1: TV\n2: Streaming\n3: Turntable\n4: CD Player\n'
                '5: Auxiliary\n6: Tuner\n7: Phono\n8: Media Server'
            )
        # 6-source amps: monoprice6, zpr68-10, sonance6
        return '1: TV\n2: Streaming\n3: Turntable\n4: CD Player\n5: Auxiliary\n6: Tuner'

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return XantechOptionsFlow(config_entry)


class XantechOptionsFlow(OptionsFlow):
    """Handle options flow for Xantech integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        """Show menu of configuration options."""
        return self.async_show_menu(
            step_id='init',
            menu_options=['polling', 'connection', 'zones', 'sources', 'features'],
        )

    async def async_step_connection(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            new_port = user_input.get(CONF_PORT, '')
            if not new_port:
                errors['base'] = 'cannot_connect'
            else:
                # update config entry data with new port
                new_data = {**self.config_entry.data, CONF_PORT: new_port}
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return self.async_create_entry(title='', data=self.config_entry.options)

        current_port = self.config_entry.data.get(CONF_PORT, '/dev/ttyUSB0')

        return self.async_show_form(
            step_id='connection',
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORT, default=current_port): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_polling(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure polling interval."""
        if user_input is not None:
            return self.async_create_entry(title='', data=user_input)

        current_scan_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        return self.async_show_form(
            step_id='polling',
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

    async def async_step_zones(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure zones."""
        errors: dict[str, str] = {}

        if user_input is not None:
            zones_text = user_input.get('zones_config', '')
            zones = self._parse_zones_config(zones_text)

            if not zones:
                errors['base'] = 'invalid_zones'
            else:
                # update config entry data with new zones
                new_data = {**self.config_entry.data, CONF_ZONES: zones}
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return self.async_create_entry(title='', data=self.config_entry.options)

        # convert current zones dict back to text
        current_zones = self.config_entry.data.get(CONF_ZONES, {})
        zones_text = self._zones_to_text(current_zones)

        return self.async_show_form(
            step_id='zones',
            data_schema=vol.Schema(
                {
                    vol.Required('zones_config', default=zones_text): TextSelector(
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
        """Configure sources."""
        errors: dict[str, str] = {}

        if user_input is not None:
            sources_text = user_input.get('sources_config', '')
            sources = self._parse_sources_config(sources_text)

            if not sources:
                errors['base'] = 'invalid_sources'
            else:
                # update config entry data with new sources
                new_data = {**self.config_entry.data, CONF_SOURCES: sources}
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return self.async_create_entry(title='', data=self.config_entry.options)

        # convert current sources dict back to text
        current_sources = self.config_entry.data.get(CONF_SOURCES, {})
        sources_text = self._sources_to_text(current_sources)

        return self.async_show_form(
            step_id='sources',
            data_schema=vol.Schema(
                {
                    vol.Required('sources_config', default=sources_text): TextSelector(
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
        """Parse zone configuration text into structured dict."""
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
        """Parse source configuration text into structured dict."""
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

    def _zones_to_text(self, zones: dict[int, dict[str, str]]) -> str:
        """Convert zones dict back to text format."""
        lines = []
        for zone_id in sorted(zones.keys()):
            name = zones[zone_id].get('name', f'Zone {zone_id}')
            lines.append(f'{zone_id}: {name}')
        return '\n'.join(lines)

    def _sources_to_text(self, sources: dict[int, dict[str, str]]) -> str:
        """Convert sources dict back to text format."""
        lines = []
        for source_id in sorted(sources.keys()):
            name = sources[source_id].get('name', f'Source {source_id}')
            lines.append(f'{source_id}: {name}')
        return '\n'.join(lines)

    async def async_step_features(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure optional features."""
        if user_input is not None:
            # update config entry data with feature settings
            new_data = {
                **self.config_entry.data,
                CONF_ENABLE_AUDIO_CONTROLS: user_input.get(
                    CONF_ENABLE_AUDIO_CONTROLS, False
                ),
            }
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            return self.async_create_entry(title='', data=self.config_entry.options)

        current_audio_controls = self.config_entry.data.get(
            CONF_ENABLE_AUDIO_CONTROLS, False
        )

        return self.async_show_form(
            step_id='features',
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENABLE_AUDIO_CONTROLS,
                        default=current_audio_controls,
                    ): BooleanSelector(),
                }
            ),
        )
