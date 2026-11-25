"""Config flow for Xantech Multi-Zone Amplifier integration."""

from __future__ import annotations

import logging
import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import json
from typing import Any

from homeassistant.config_entries import (
    SOURCE_ZEROCONF,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SerialPortSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pyxantech import async_get_amp_controller, get_device_config
from serialx import SerialException
import voluptuous as vol

from . import settings
from .connection import async_close_amp
from .const import (
    AMP_TYPE_DAX88,
    CONF_AMP_TYPE,
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_DEVICE_LABEL,
    CONF_ENABLE_AUDIO_CONTROLS,
    CONF_FALLBACK_PORT,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SOURCES,
    CONF_TRANSPORT,
    CONF_ZONES,
    CONNECTION_MODES,
    DEFAULT_AMP_TYPE,
    DEFAULT_NAME,
    DEFAULT_NETWORK_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FACTORY_RESET_ENABLE_MINUTES,
    FACTORY_RESET_PHRASE,
    MODE_NETWORK,
    MODE_SERIAL,
    SUPPORTED_AMP_TYPES,
    TRANSPORT_NETWORK,
    TRANSPORT_SERIAL,
)
from .dax88_network import (
    MAX_NAME_LENGTH,
    MAX_TOTAL_NAME_CHARACTERS,
    STANDBY_MAX_STEPS,
    STANDBY_STEP_MINUTES,
    Dax88NetworkAmp,
    Dax88NetworkError,
    check_name,
)
from .network import (
    NotADax88Error,
    async_check_control_port,
    async_check_dax88,
    async_check_linkplay_amp,
    connection_mode,
    factory_reset_enabled,
    ports_in_use,
    uses_fallback_port,
    uses_network,
    uses_serial,
)
from .port import port_label
from .settings import AmpSettingsError

LOG = logging.getLogger(__name__)

# how long a page waits for the entry to reload before showing the menu again
RELOAD_WAIT_SECONDS = 30

# type alias to reduce line length in signatures
ZonesDict = dict[int, dict[str, str]]
SourcesDict = dict[int, dict[str, str]]

TEXT = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))


def _optional(key: str, current: str | None) -> vol.Optional:
    """An optional field that shows its current value, if it has one."""
    if current:
        return vol.Optional(key, description={'suggested_value': current})
    return vol.Optional(key)

# zones offered ticked for a new amplifier (the first controller's zones)
_DEFAULT_ZONE_IDS: dict[str, range] = {
    'dax88': range(11, 19),
    'xantech8': range(11, 17),
    'monoprice6': range(11, 17),
}
_EXAMPLE_SOURCES = (
    'TV',
    'Streaming',
    'Turntable',
    'CD Player',
    'Auxiliary',
    'Tuner',
    'Phono',
    'Media Server',
)


# --- zones and sources: one field each, shared by setup and options ---


def valid_zone_ids(amp_type: str | None) -> list[int]:
    """Every zone id the amplifier type accepts."""
    if not amp_type:
        return list(range(11, 19))
    zones = get_device_config(amp_type, 'zones') or {}
    alt = get_device_config(amp_type, 'alternative_zones', log_missing=False) or {}
    return sorted({int(z) for z in {**zones, **alt}}) or list(range(11, 19))


def flat_zone_numbers(amp_type: str | None) -> bool:
    """True for a model that numbers zones from 1 (a ZPR68: 1 to 15).

    On such a model 11 is the eleventh zone, not zone 1 of the first amplifier, so
    its zone ids are shown as they are.
    """
    if not amp_type:
        return False
    try:
        return any(z <= 10 for z in valid_zone_ids(amp_type))
    except KeyError:  # a model the library does not describe
        return False


def zone_label(
    zone_id: int, name: str | None = None, amp_type: str | None = None
) -> str:
    """How a zone is shown: its number on the amplifier, then its name.

    Most models address their zones as 11-18 (and 21-28, 31-38 for chained
    amplifiers): the first digit is the amplifier, the second the zone printed on it.
    So 11 is shown as "Zone 1", 23 as "Amplifier 2, zone 3". A model that numbers
    its zones from 1 keeps every number as it is (see flat_zone_numbers): there,
    "Zone 1" for 11 would be the label of another zone.
    """
    if zone_id > 10 and not flat_zone_numbers(amp_type):
        unit, zone = divmod(zone_id, 10)
    else:
        unit, zone = 1, zone_id
    label = f'Zone {zone}' if unit == 1 else f'Amplifier {unit}, zone {zone}'
    return f'{label} ({name})' if name else label


def default_zone_ids(amp_type: str | None) -> list[int]:
    """The zones ticked by default for a new amplifier."""
    valid = valid_zone_ids(amp_type)
    wanted = [z for z in _DEFAULT_ZONE_IDS.get(amp_type or '', ()) if z in valid]
    return wanted or valid[:6]


def source_count(amp_type: str | None) -> int:
    count = (
        get_device_config(amp_type, 'num_sources', log_missing=False) if amp_type else 0
    )
    return int(count or 8)


def zones_select_schema(
    amp_type: str | None,
    selected: list[int],
    *,
    names: dict[int, str] | None = None,
    audio_controls: bool | None = None,
) -> vol.Schema:
    """Tick the zones that are in use (and, on first setup, the tone controls)."""
    options = [
        SelectOptionDict(
            value=str(z), label=zone_label(z, (names or {}).get(z), amp_type)
        )
        for z in valid_zone_ids(amp_type)
    ]
    schema: dict[Any, Any] = {
        vol.Required('zone_ids', default=[str(z) for z in selected]): SelectSelector(
            SelectSelectorConfig(
                options=options, multiple=True, mode=SelectSelectorMode.LIST
            )
        ),
    }
    if audio_controls is not None:
        schema[vol.Optional(CONF_ENABLE_AUDIO_CONTROLS, default=audio_controls)] = (
            BooleanSelector()
        )
    return vol.Schema(schema)


def read_zone_ids(amp_type: str | None, user_input: dict[str, Any]) -> list[int]:
    valid = set(valid_zone_ids(amp_type))
    return sorted(int(z) for z in user_input.get('zone_ids', []) if int(z) in valid)


def zone_names_schema(
    zone_ids: list[int], names: dict[int, str], amp_type: str | None = None
) -> vol.Schema:
    """One name field per zone."""
    return vol.Schema(
        {
            vol.Required(
                f'zone_{z}', default=names.get(z) or zone_label(z, None, amp_type)
            ): TEXT
            for z in zone_ids
        }
    )


def read_zone_names(
    zone_ids: list[int], user_input: dict[str, Any], amp_type: str | None = None
) -> ZonesDict:
    return {
        z: {
            'name': str(user_input.get(f'zone_{z}') or '').strip()
            or zone_label(z, None, amp_type)
        }
        for z in zone_ids
    }


def sources_schema(amp_type: str | None, names: dict[int, str]) -> vol.Schema:
    """One optional name field per input; an empty field hides that input."""
    return vol.Schema(
        {
            vol.Optional(
                f'source_{s}', description={'suggested_value': names.get(s, '')}
            ): TEXT
            for s in range(1, source_count(amp_type) + 1)
        }
    )


def read_sources(amp_type: str | None, user_input: dict[str, Any]) -> SourcesDict:
    sources: SourcesDict = {}
    for s in range(1, source_count(amp_type) + 1):
        name = str(user_input.get(f'source_{s}') or '').strip()
        if name:
            sources[s] = {'name': name}
    return sources


def _stored(value: Any) -> Any:
    """A value as Home Assistant keeps it on disk (JSON: every dict key is text)."""
    return json.loads(json.dumps(value))


def _names_of(config: dict[Any, dict[str, str]]) -> dict[int, str]:
    """Stored zones/sources (keys may be str after a restart) -> {id: name}."""
    return {int(k): v.get('name', '') for k, v in config.items()}


class XantechConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xantech Multi-Zone Amplifier."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._amp_names: dict[str, Any] | None = None  # names read from the amp
        self._discovered: dict[str, str] = {}
        self._zone_ids: list[int] = []

    def _abort_if_amp_set_up(self, device_id: str, host: str) -> None:
        """Stop if an entry already has this amplifier, in any connection mode.

        An entry that began as RS-232 keeps its RS-232 unique id, so the usual
        unique-id check cannot see that its network module is this one. The entry's
        address is kept current on the way.
        """
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_DEVICE_ID) == device_id:
                if entry.data.get(CONF_HOST) != host:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_HOST: host}
                    )
                    # A loaded entry reloads by its own update listener; asking for
                    # a second reload opened and closed the connections twice.
                    if entry.state is not ConfigEntryState.LOADED:
                        self.hass.config_entries.async_schedule_reload(entry.entry_id)
                raise AbortFlow('already_configured')

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        """Choose how Home Assistant talks to the amplifier."""
        return self.async_show_menu(step_id='user', menu_options=['serial', 'network'])

    async def async_step_network(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Connect over the amplifier's built-in Wi-Fi/LAN (Linkplay) module."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            amp = None
            try:
                module = await async_check_linkplay_amp(self.hass, host)
                device_id = module.get('uuid') or module.get('MAC') or host
                # also when a "Discovered" card is confirmed after the amplifier
                # became part of another entry
                self._abort_if_amp_set_up(device_id, host)
                await self.async_set_unique_id(f'{DOMAIN}_dax88_{device_id}')
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                amp = Dax88NetworkAmp(
                    host, DEFAULT_NETWORK_PORT, timeout=3.0, retries=3
                )
                self._amp_names = await amp.names()
            except NotADax88Error:
                errors['base'] = 'not_dax88'
            except (Dax88NetworkError, OSError, TimeoutError, ValueError):
                LOG.debug('Network connection test to %s failed', host, exc_info=True)
                errors['base'] = 'cannot_connect_network'
            else:
                self._data = {
                    CONF_TRANSPORT: TRANSPORT_NETWORK,
                    CONF_CONNECTION_MODE: MODE_NETWORK,
                    CONF_HOST: host,
                    CONF_NETWORK_PORT: DEFAULT_NETWORK_PORT,
                    CONF_DEVICE_ID: device_id,
                    # shown as the device name in Home Assistant
                    CONF_DEVICE_LABEL: self._amp_names['device'],
                    # the network link speaks the DAX88 protocol, whatever the badge
                    CONF_AMP_TYPE: AMP_TYPE_DAX88,
                }
                return await self.async_step_zones()
            finally:
                if amp is not None:
                    amp.close()

        return self.async_show_form(
            step_id='network',
            data_schema=vol.Schema({vol.Required(CONF_HOST): TEXT}),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """A Linkplay device was found on the LAN: offer it only if it is a DAX88."""
        host = discovery_info.host
        try:
            module = await async_check_dax88(self.hass, host)
        except NotADax88Error:
            return self.async_abort(reason='not_dax88')
        except (OSError, TimeoutError, ValueError):
            return self.async_abort(reason='cannot_connect_network')
        device_id = module.get('uuid') or module.get('MAC') or host
        self._abort_if_amp_set_up(device_id, host)
        await self.async_set_unique_id(f'{DOMAIN}_dax88_{device_id}')
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._discovered = {'host': host, 'name': module.get('DeviceName') or host}
        self.context['title_placeholders'] = {'name': self._discovered['name']}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm adding a discovered DAX88, then continue as a network setup."""
        if user_input is not None:
            return await self.async_step_network({CONF_HOST: self._discovered['host']})
        return self.async_show_form(
            step_id='zeroconf_confirm',
            description_placeholders=dict(self._discovered),
        )

    async def async_step_serial(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Connect over RS-232 (serial port or serial-over-IP URL)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            port = user_input[CONF_PORT]
            amp_type = user_input[CONF_AMP_TYPE]

            # test connection to amplifier
            amp = None
            try:
                # before the port is opened: an amplifier that is set up already
                # is using it
                await self.async_set_unique_id(f'{DOMAIN}_{port}')
                self._abort_if_unique_id_configured()
                # An entry keeps its first unique id when its port is changed in the
                # options, so that check does not see the port it uses now, nor
                # its second link. Two entries on one port answer each other's
                # commands.
                others = [
                    entry
                    for entry in self._async_current_entries(include_ignore=False)
                    if not entry.disabled_by
                ]
                if port in ports_in_use(others):
                    errors[CONF_PORT] = 'port_in_use'
                else:
                    amp = await async_get_amp_controller(
                        amp_type, port, self.hass.loop
                    )
                if amp:
                    # try to verify communication works
                    # we'll just store the config for now
                    self._data = {
                        **user_input,
                        CONF_TRANSPORT: TRANSPORT_SERIAL,
                        CONF_CONNECTION_MODE: MODE_SERIAL,
                    }
                    return await self.async_step_zones()

            except AbortFlow:
                raise  # "already configured" is not an unexpected error
            except (OSError, TimeoutError, SerialException):
                LOG.exception('Serial connection error to %s', port_label(port))
                errors['base'] = 'cannot_connect'
            except Exception:
                LOG.exception('Unexpected error during connection test')
                errors['base'] = 'unknown'
            finally:
                # release the port; setup opens its own connection
                async_close_amp(amp)

        return self.async_show_form(
            step_id='serial',
            data_schema=vol.Schema(
                {
                    # lists local ports and ESPHome serial proxy ports (e.g. the
                    # AUX-2 RS-232 ports); "Enter manually" still takes a
                    # /dev path or a socket://host:port URL
                    vol.Required(CONF_PORT): SerialPortSelector(),
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
        """Tick the zones that are in use."""
        errors: dict[str, str] = {}
        amp_type = self._data.get(CONF_AMP_TYPE)

        if user_input is not None:
            self._zone_ids = read_zone_ids(amp_type, user_input)
            if not self._zone_ids:
                errors['base'] = 'invalid_zones'
            else:
                self._data[CONF_ENABLE_AUDIO_CONTROLS] = user_input.get(
                    CONF_ENABLE_AUDIO_CONTROLS, False
                )
                return await self.async_step_zone_names()

        return self.async_show_form(
            step_id='zones',
            data_schema=zones_select_schema(
                amp_type,
                default_zone_ids(amp_type),
                names=self._amp_zone_names(),
                audio_controls=False,
            ),
            errors=errors,
        )

    def _amp_zone_names(self) -> dict[int, str]:
        """Zone names read from the amplifier (network setup), by Home Assistant id."""
        if not self._amp_names:
            return {}
        # the amplifier numbers its zones 1-8; Home Assistant uses 11-18
        return {10 + z: name for z, name in self._amp_names['zones'].items()}

    async def async_step_zone_names(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Name each zone (pre-filled from the amplifier when it told us)."""
        if user_input is not None:
            self._data[CONF_ZONES] = read_zone_names(
                self._zone_ids, user_input, self._data.get(CONF_AMP_TYPE)
            )
            return await self.async_step_sources()

        return self.async_show_form(
            step_id='zone_names',
            data_schema=zone_names_schema(
                self._zone_ids, self._amp_zone_names(), self._data.get(CONF_AMP_TYPE)
            ),
        )

    async def async_step_sources(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Name the inputs that are in use."""
        errors: dict[str, str] = {}
        amp_type = self._data.get(CONF_AMP_TYPE)

        if user_input is not None:
            sources = read_sources(amp_type, user_input)
            if not sources:
                errors['base'] = 'invalid_sources'
            else:
                self._data[CONF_SOURCES] = sources
                # a port's label: an ESPHome proxy URL shows as its port name
                where = port_label(self._data.get(CONF_PORT) or '') or self._data.get(
                    CONF_HOST
                )
                if self._amp_names:
                    where = self._amp_names['device']
                return self.async_create_entry(
                    title=f'{DEFAULT_NAME} ({where})',
                    data=self._data,
                )

        if self._amp_names:
            # input 8 is the amplifier's streaming input and has no name of its own
            names = {**self._amp_names['inputs'], 8: 'Streaming'}
        else:
            names = dict(enumerate(_EXAMPLE_SOURCES, start=1))
        return self.async_show_form(
            step_id='sources',
            data_schema=sources_schema(amp_type, names),
            errors=errors,
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
        self._config_entry = config_entry
        self._zone_ids: list[int] = []
        self._amp_names: dict[str, Any] | None = None
        self._task: asyncio.Task[dict[str, str]] | None = None
        self._result: dict[str, str] = {}
        self._failure = 'unknown'
        self._restore_page = 'restore_done'  # which result page a restore ends on
        self._failure_details: dict[str, str] = {}
        self._pending: dict[str, Any] = {}

    # --- helpers ---

    def _done(self) -> ConfigFlowResult:
        """Close the flow, keeping the options as they are now."""
        return self.async_create_entry(title='', data=dict(self._config_entry.options))

    async def _settled(self) -> None:
        """Wait for the reload that a saved change starts.

        The pages that talk to the amplifier need it connected again, and the menu
        depends on the connection that is now in use.
        """
        await asyncio.sleep(0)  # let the entry's update listener begin
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(RELOAD_WAIT_SECONDS):
                async with self._config_entry.setup_lock:  # held while it reloads
                    pass

    async def _to_menu(self) -> ConfigFlowResult:
        """Back to the menu after a page, so the dialog stays open.

        Home Assistant's dialog has no back button; closing after every page meant
        opening the settings again for each change.
        """
        await self._settled()
        # nothing read for one page is carried over to the next visit
        self._amp_names = None
        self._pending = {}
        self._zone_ids = []
        return await self.async_step_init()

    async def async_step_done(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        """Close the dialog. Every page was already saved when it was submitted."""
        return self._done()

    def _close_discoveries(self, device_id: str | None) -> None:
        """Take away the "Discovered" card of the amplifier this entry now includes.

        Left open, it would keep offering to add the same amplifier a second time.
        """
        if not device_id:
            return
        flows = self.hass.config_entries.flow
        for flow in flows.async_progress_by_handler(DOMAIN):
            context = flow['context']
            if (
                context.get('source') == SOURCE_ZEROCONF
                and context.get('unique_id') == f'{DOMAIN}_dax88_{device_id}'
            ):
                flows.async_abort(flow['flow_id'])

    async def _save_data(self, **changes: Any) -> ConfigFlowResult:
        """Store changed entry data (the entry reloads) and go back to the menu.

        Compared as it is stored: after a restart the zone and input numbers are
        text ("11"), a page gives numbers (11), and that alone is not a change worth
        a reload.
        """
        data = dict(self._config_entry.data)
        if any(_stored(data.get(key)) != _stored(new) for key, new in changes.items()):
            self._update_data({**data, **changes})
        return await self._to_menu()

    def _update_data(self, data: dict[str, Any]) -> None:
        """Store new entry data and make sure the entry is set up with it.

        A loaded entry reloads by its update listener. An entry whose setup failed
        has no listener yet: without the reload asked for here, a corrected port
        or address would wait for Home Assistant's next retry.
        """
        entry = self._config_entry
        self.hass.config_entries.async_update_entry(entry, data=data)
        if entry.state in (
            ConfigEntryState.SETUP_RETRY,
            ConfigEntryState.SETUP_ERROR,
        ):
            self.hass.config_entries.async_schedule_reload(entry.entry_id)

    async def _progress(
        self,
        step_id: str,
        action: str,
        job: Callable[[], Awaitable[dict[str, str]]],
        done_step: str,
    ) -> ConfigFlowResult:
        """Run a slow amplifier job with a progress screen, then go to ``done_step``."""
        started = self._task is None
        if self._task is None:
            self._task = self.hass.async_create_task(job())
        if started or not self._task.done():
            # Always show the progress screen first, even if the job is already done:
            # Home Assistant would otherwise hand this step's input to ``done_step``,
            # which would take it as its own "OK" and close without showing the result.
            return self.async_show_progress(
                step_id=step_id, progress_action=action, progress_task=self._task
            )
        task, self._task = self._task, None
        try:
            self._result = task.result()
        except AmpSettingsError as err:
            LOG.warning('%s', err)
            self._failure, self._failure_details = err.key, err.placeholders
            return self.async_show_progress_done(next_step_id='action_failed')
        except Exception:
            LOG.exception('Amplifier settings job failed')
            self._failure, self._failure_details = 'unknown', {}
            return self.async_show_progress_done(next_step_id='action_failed')
        return self.async_show_progress_done(next_step_id=done_step)

    async def async_step_action_failed(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        return self.async_abort(
            reason=self._failure, description_placeholders=self._failure_details
        )

    # --- menu ---

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        """Show menu of configuration options."""
        options = ['connection', 'zones', 'sources', 'features', 'polling']
        if uses_network(connection_mode(dict(self._config_entry.data))):
            # these live on the amplifier and need its network module
            options += ['amp_names', 'standby', 'backup', 'factory_reset']
        return self.async_show_menu(step_id='init', menu_options=[*options, 'done'])

    # --- connection ---

    async def async_step_connection(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose the connection: one link only, or a preferred link with fallback."""
        errors: dict[str, str] = {}
        data = dict(self._config_entry.data)

        if user_input is not None:
            mode = user_input[CONF_CONNECTION_MODE]
            port = (user_input.get(CONF_PORT) or '').strip()
            fallback_port = (user_input.get(CONF_FALLBACK_PORT) or '').strip()
            host = (user_input.get(CONF_HOST) or '').strip()
            new = {
                **data,
                CONF_CONNECTION_MODE: mode,
                CONF_PORT: port,
                CONF_FALLBACK_PORT: fallback_port,
                CONF_HOST: host,
            }
            new[CONF_TRANSPORT] = (
                TRANSPORT_SERIAL
                if mode == MODE_SERIAL
                else (
                    TRANSPORT_NETWORK
                    if mode == MODE_NETWORK
                    else data.get(CONF_TRANSPORT)
                )
            )
            # ports and modules already used by another amplifier of this integration
            others = [
                e
                for e in self.hass.config_entries.async_entries(DOMAIN)
                if e.entry_id != self._config_entry.entry_id and not e.disabled_by
            ]
            taken_ports = ports_in_use(others)
            if uses_serial(mode) and not port:
                errors[CONF_PORT] = 'port_required'
            elif uses_serial(mode) and port in taken_ports:
                errors[CONF_PORT] = 'port_in_use'
            if uses_fallback_port(mode) and not fallback_port:
                errors[CONF_FALLBACK_PORT] = 'fallback_port_required'
            elif uses_fallback_port(mode) and fallback_port == port:
                errors[CONF_FALLBACK_PORT] = 'same_port'
            elif uses_fallback_port(mode) and fallback_port in taken_ports:
                errors[CONF_FALLBACK_PORT] = 'port_in_use'
            if uses_network(mode):
                if not host:
                    errors[CONF_HOST] = 'host_required'
                else:
                    try:
                        module = await async_check_linkplay_amp(self.hass, host)
                    except NotADax88Error:
                        errors['base'] = 'no_linkplay_control'
                    except (OSError, TimeoutError, ValueError):
                        errors['base'] = 'cannot_connect_network'
                    else:
                        known = data.get(CONF_DEVICE_ID)
                        if known == data.get(CONF_HOST):
                            known = None  # setup had no identifier, only the address
                        # setup stores the uuid, or the MAC address when there is none
                        found = {module.get('uuid'), module.get('MAC')} - {None, ''}
                        if known and found and known not in found:
                            errors['base'] = 'different_amp'
                        elif any(
                            other.data.get(CONF_DEVICE_ID) in found for other in others
                        ):
                            errors['base'] = 'amp_in_other_entry'
                        else:
                            new[CONF_DEVICE_ID] = (
                                known or module.get('uuid') or module.get('MAC')
                            )
                        newly_used = host != data.get(CONF_HOST) or not uses_network(
                            connection_mode(data)
                        )
                        if not errors and newly_used and not module.get('probed'):
                            # The module's web interface answered. Setup also tries
                            # the connection the amplifier is controlled over; a
                            # link that is new here must pass the same test, or a
                            # working entry is reloaded onto one that does not.
                            try:
                                await async_check_control_port(host)
                            except (Dax88NetworkError, ValueError):
                                errors['base'] = 'no_amp_control'
            if not errors:
                self._update_data(new)
                self._close_discoveries(new.get(CONF_DEVICE_ID))
                return await self._to_menu()

        return self.async_show_form(
            step_id='connection',
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONNECTION_MODE, default=connection_mode(data)
                    ): SelectSelector(
                        SelectSelectorConfig(
                            # every amp type: the links are checked, not the model
                            options=CONNECTION_MODES,
                            mode=SelectSelectorMode.LIST,
                            translation_key='connection_mode',
                        )
                    ),
                    # Home Assistant's port picker (local ports and ESPHome serial
                    # proxy ports); "Enter manually" takes a path or socket:// URL
                    _optional(CONF_PORT, data.get(CONF_PORT)): SerialPortSelector(),
                    _optional(
                        CONF_FALLBACK_PORT, data.get(CONF_FALLBACK_PORT)
                    ): SerialPortSelector(),
                    _optional(CONF_HOST, data.get(CONF_HOST)): TEXT,
                }
            ),
            errors=errors,
        )

    # --- polling, features ---

    async def async_step_polling(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure polling interval."""
        if user_input is not None:
            # keep the other options (e.g. the factory reset switch) as they are
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                options={**self._config_entry.options, **user_input},
            )
            return await self._to_menu()

        current_scan_interval = self._config_entry.options.get(
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

    async def async_step_features(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure optional features."""
        if user_input is not None:
            return await self._save_data(
                **{
                    CONF_ENABLE_AUDIO_CONTROLS: user_input.get(
                        CONF_ENABLE_AUDIO_CONTROLS, False
                    )
                }
            )

        return self.async_show_form(
            step_id='features',
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENABLE_AUDIO_CONTROLS,
                        default=self._config_entry.data.get(
                            CONF_ENABLE_AUDIO_CONTROLS, False
                        ),
                    ): BooleanSelector(),
                }
            ),
        )

    # --- zones and sources ---

    async def async_step_zones(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Tick the zones that are in use."""
        errors: dict[str, str] = {}
        amp_type = self._config_entry.data.get(CONF_AMP_TYPE)

        if user_input is not None:
            self._zone_ids = read_zone_ids(amp_type, user_input)
            if not self._zone_ids:
                errors['base'] = 'invalid_zones'
            else:
                return await self.async_step_zone_names()

        current = sorted(_names_of(self._config_entry.data.get(CONF_ZONES, {})))
        return self.async_show_form(
            step_id='zones',
            data_schema=zones_select_schema(
                amp_type, current, names=await self._zone_label_names()
            ),
            errors=errors,
            last_step=False,  # the names follow
        )

    async def _zone_label_names(self) -> dict[int, str]:
        """The name shown next to each zone number.

        Home Assistant's own name for a zone that is in use; for the others the name
        stored on the amplifier, when its network module can be asked.
        """
        names: dict[int, str] = {}
        try:
            stored = await self._amp_client().names()
        except (AmpSettingsError, Dax88NetworkError, OSError, TimeoutError):
            pass  # no network module, or no answer: Home Assistant's names only
        else:
            # the amplifier numbers its zones 1-8; Home Assistant uses 11-18
            names = {10 + z: name for z, name in stored['zones'].items()}
        configured = _names_of(self._config_entry.data.get(CONF_ZONES, {}))
        names.update({z: name for z, name in configured.items() if name})
        return names

    async def async_step_zone_names(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Name each zone."""
        if user_input is not None:
            return await self._save_data(
                **{
                    CONF_ZONES: read_zone_names(
                        self._zone_ids,
                        user_input,
                        self._config_entry.data.get(CONF_AMP_TYPE),
                    )
                }
            )
        names = _names_of(self._config_entry.data.get(CONF_ZONES, {}))
        return self.async_show_form(
            step_id='zone_names',
            data_schema=zone_names_schema(
                self._zone_ids, names, self._config_entry.data.get(CONF_AMP_TYPE)
            ),
        )

    async def async_step_sources(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Name the inputs that are in use."""
        errors: dict[str, str] = {}
        amp_type = self._config_entry.data.get(CONF_AMP_TYPE)

        if user_input is not None:
            sources = read_sources(amp_type, user_input)
            if not sources:
                errors['base'] = 'invalid_sources'
            else:
                return await self._save_data(**{CONF_SOURCES: sources})

        names = _names_of(self._config_entry.data.get(CONF_SOURCES, {}))
        return self.async_show_form(
            step_id='sources',
            data_schema=sources_schema(amp_type, names),
            errors=errors,
        )

    # --- settings kept on the amplifier (network module) ---

    def _amp_client(self) -> Any:
        """The loaded entry's network client (raises AmpSettingsError with a reason)."""
        return settings.network_amp(self.hass, self._config_entry.entry_id)[1]

    async def async_step_amp_names(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """The device, zone and input names stored on the amplifier."""
        try:
            client = self._amp_client()
            if self._amp_names is None:
                self._amp_names = await client.names()
        except AmpSettingsError as err:
            return self.async_abort(
                reason=err.key, description_placeholders=err.placeholders
            )
        except Dax88NetworkError:
            return self.async_abort(reason='amp_no_answer')
        stored = self._amp_names
        fields: dict[str, str] = {
            'device_name': stored['device'],
            **{f'amp_zone_{z}': n for z, n in stored['zones'].items()},
            **{f'amp_input_{i}': n for i, n in stored['inputs'].items()},
        }
        errors: dict[str, str] = {}

        if user_input is not None:
            wanted = {key: str(user_input.get(key) or '').strip() for key in fields}
            for key, name in wanted.items():
                try:
                    check_name(name)
                except ValueError:
                    errors[key] = 'name_invalid'
            if sum(len(n) for n in wanted.values()) > MAX_TOTAL_NAME_CHARACTERS:
                errors['base'] = 'names_total'
            if not errors:
                try:
                    await client.set_names(
                        device=wanted['device_name'],
                        zones={z: wanted[f'amp_zone_{z}'] for z in stored['zones']},
                        inputs={i: wanted[f'amp_input_{i}'] for i in stored['inputs']},
                    )
                except ValueError:
                    errors['base'] = 'names_total'
                except Dax88NetworkError:
                    LOG.warning('Renaming on the amplifier failed', exc_info=True)
                    # Some names may have been written before it stopped answering:
                    # not "nothing was changed", and read them again before the next
                    # try instead of comparing with what was read earlier.
                    self._amp_names = None
                    errors['base'] = 'amp_stopped_answering'
                else:
                    if user_input.get('use_in_ha'):
                        return await self._save_data(
                            **self._ha_names_from_amp(wanted)
                        )
                    return await self._to_menu()
            fields = wanted

        return self.async_show_form(
            step_id='amp_names',
            data_schema=vol.Schema(
                {
                    **{
                        vol.Required(key, default=value): TEXT
                        for key, value in fields.items()
                    },
                    vol.Optional('use_in_ha', default=False): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                'used': str(sum(len(n) for n in fields.values())),
                'limit': str(MAX_TOTAL_NAME_CHARACTERS),
                'max': str(MAX_NAME_LENGTH),
            },
        )

    def _ha_names_from_amp(self, wanted: dict[str, str]) -> dict[str, Any]:
        """Entry data with the zone and source names copied from the amplifier.

        Only renames what Home Assistant already has: the choice of zones and inputs
        stays as it is. The amplifier numbers zones 1-8; Home Assistant may use 11-18.
        """
        zones: ZonesDict = {}
        for zone_id, name in _names_of(
            self._config_entry.data.get(CONF_ZONES, {})
        ).items():
            amp_zone = zone_id - 10 if 11 <= zone_id <= 18 else zone_id
            zones[zone_id] = {'name': wanted.get(f'amp_zone_{amp_zone}') or name}
        sources: SourcesDict = {}
        for source_id, name in _names_of(
            self._config_entry.data.get(CONF_SOURCES, {})
        ).items():
            sources[source_id] = {'name': wanted.get(f'amp_input_{source_id}') or name}
        return {CONF_ZONES: zones, CONF_SOURCES: sources}

    async def async_step_standby(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Auto-standby: switch all zones off after a time without an input signal."""
        errors: dict[str, str] = {}
        try:
            client = self._amp_client()
            current = await client.get_auto_standby()
        except AmpSettingsError as err:
            return self.async_abort(
                reason=err.key, description_placeholders=err.placeholders
            )
        except Dax88NetworkError:
            return self.async_abort(reason='amp_no_answer')

        if user_input is not None:
            minutes: int | None = None
            if user_input.get('standby_enabled'):
                minutes = int(user_input.get('standby_hours', 0)) * 60 + int(
                    user_input.get('standby_minutes', '0')
                )
                limit = STANDBY_MAX_STEPS * STANDBY_STEP_MINUTES
                if not STANDBY_STEP_MINUTES <= minutes <= limit:
                    errors['base'] = 'standby_range'
            if not errors:
                try:
                    if minutes != current:
                        await client.set_auto_standby(minutes)
                        if await client.get_auto_standby() != minutes:
                            errors['base'] = 'not_stored'
                except Dax88NetworkError:
                    # the new time may have been stored before it stopped answering
                    errors['base'] = 'amp_stopped_answering'
                if not errors:
                    return await self._to_menu()

        shown = current if current is not None else 120
        return self.async_show_form(
            step_id='standby',
            data_schema=vol.Schema(
                {
                    vol.Required(
                        'standby_enabled', default=current is not None
                    ): BooleanSelector(),
                    vol.Required('standby_hours', default=shown // 60): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=STANDBY_MAX_STEPS // 2,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement='hours',
                        )
                    ),
                    vol.Required(
                        'standby_minutes', default=str(shown % 60)
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=['0', '30'],
                            mode=SelectSelectorMode.LIST,
                            translation_key='standby_minutes',
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # --- backup and restore ---

    async def async_step_backup(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        # 'init' is the way back: the dialog itself has no back button
        return self.async_show_menu(
            step_id='backup', menu_options=['backup_now', 'restore_backup', 'init']
        )

    async def async_step_backup_now(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        """Save the amplifier's settings to a new backup file."""
        try:
            name = await settings.async_export(self.hass, self._config_entry)
        except AmpSettingsError as err:
            return self.async_abort(
                reason=err.key, description_placeholders=err.placeholders
            )
        self._result = {'backup_file': name}
        return await self.async_step_backup_done()

    async def async_step_backup_done(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._to_menu()
        return self.async_show_form(
            step_id='backup_done',
            data_schema=vol.Schema({}),
            description_placeholders=self._result,
            last_step=False,  # "Next": it leads back to the menu
        )

    async def async_step_restore_backup(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose a backup file to put back on the amplifier."""
        # this amplifier's own backups first, so the one offered is never another
        # amplifier's while this one has a backup; those are listed last and marked
        files = await settings.async_list_backups(self.hass, self._config_entry)
        if not files:
            return self.async_abort(reason='no_backups')
        if user_input is not None:
            self._pending = dict(user_input)
            return await self.async_step_restore_run()
        return self.async_show_form(
            step_id='restore_backup',
            data_schema=vol.Schema(
                {
                    vol.Required('backup_file', default=files[0][0]): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=name,
                                    label=f'{name} (another amplifier)'
                                    if other
                                    else name,
                                )
                                for name, other in files
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional('include_power', default=False): BooleanSelector(),
                }
            ),
        )

    async def async_step_restore_run(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        async def job() -> dict[str, str]:
            include_power = self._pending.get('include_power', False)
            left_alone = list(
                await settings.async_import(
                    self.hass,
                    self._config_entry,
                    self._pending['backup_file'],
                    include_power=include_power,
                )
                or []
            )
            # which result page: what was restored is not the same in the three cases
            if left_alone:
                self._restore_page = 'restore_done_left_alone'
            elif include_power:
                self._restore_page = 'restore_done'
            else:
                self._restore_page = 'restore_done_no_power'
            return {
                'backup_file': self._pending['backup_file'],
                'zones': settings.zone_list(left_alone) if left_alone else '',
            }

        return await self._progress('restore_run', 'restoring', job, 'restore_done')

    async def async_step_restore_done(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._to_menu()
        return self.async_show_form(
            step_id=self._restore_page,
            data_schema=vol.Schema({}),
            description_placeholders=self._result,
            last_step=False,
        )

    # the same page with another text; "Next" leads back to the menu from each
    async_step_restore_done_no_power = async_step_restore_done
    async_step_restore_done_left_alone = async_step_restore_done

    # --- factory reset: kill switch, typed phrase, backup first ---

    async def async_step_factory_reset(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        """The reset itself is only offered while the kill switch is on."""
        options = ['factory_reset_switch']
        if factory_reset_enabled(dict(self._config_entry.options)):
            options.append('factory_reset_now')
        return self.async_show_menu(
            step_id='factory_reset', menu_options=[*options, 'init']
        )

    async def async_step_factory_reset_switch(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """The factory reset kill switch: off by default, turns itself off again."""
        options = dict(self._config_entry.options)
        if user_input is not None:
            enable = bool(user_input.get('allow_factory_reset'))
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                options=settings.enable_factory_reset_options(options, enable),
            )
            if enable:
                # on: straight to the page that now offers the reset
                await self._settled()
                return await self.async_step_factory_reset()
            return await self._to_menu()

        return self.async_show_form(
            step_id='factory_reset_switch',
            data_schema=vol.Schema(
                {
                    vol.Required(
                        'allow_factory_reset',
                        default=factory_reset_enabled(options),
                    ): BooleanSelector(),
                }
            ),
            description_placeholders={'minutes': str(FACTORY_RESET_ENABLE_MINUTES)},
        )

    async def async_step_factory_reset_now(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Type the phrase to reset. A backup is always saved first."""
        errors: dict[str, str] = {}
        if not factory_reset_enabled(dict(self._config_entry.options)):
            return self.async_abort(reason='switch_off')
        if user_input is not None:
            if user_input.get('confirm') != FACTORY_RESET_PHRASE:
                errors['confirm'] = 'confirm_mismatch'
            else:
                self._pending = dict(user_input)
                return await self.async_step_factory_reset_run()
        return self.async_show_form(
            step_id='factory_reset_now',
            data_schema=vol.Schema({vol.Required('confirm'): TEXT}),
            errors=errors,
            description_placeholders={'phrase': FACTORY_RESET_PHRASE},
        )

    async def async_step_factory_reset_run(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        async def job() -> dict[str, str]:
            backup = await settings.async_factory_reset(
                self.hass, self._config_entry, confirm=self._pending['confirm']
            )
            return {'backup_file': backup}

        return await self._progress(
            'factory_reset_run', 'resetting', job, 'factory_reset_done'
        )

    async def async_step_factory_reset_done(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._to_menu()
        return self.async_show_form(
            step_id='factory_reset_done',
            data_schema=vol.Schema({}),
            description_placeholders=self._result,
            last_step=False,
        )
