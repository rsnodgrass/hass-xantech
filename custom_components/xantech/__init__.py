"""Xantech Multi-Zone Amplifier Control for Home Assistant."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType
from pyxantech import async_get_amp_controller
from serialx import SerialException
import voluptuous as vol

from . import settings
from .audio_controls import apply_library_limit_overrides, restorable_settings
from .connection import async_close_amp, async_socket_reachable
from .const import (
    AMP_TYPE_DAX88,
    CONF_AMP_NAME,
    CONF_AMP_TYPE,
    CONF_DEVICE_ID,
    CONF_ENABLE_AUDIO_CONTROLS,
    CONF_FACTORY_RESET_ENABLED_UNTIL,
    CONF_FALLBACK_PORT,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SOURCES,
    CONF_ZONES,
    DEFAULT_NETWORK_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MODE_NETWORK,
    PLATFORMS,
    SERVICE_EXPORT_SETTINGS,
    SERVICE_FACTORY_RESET,
    SERVICE_IMPORT_SETTINGS,
    SERVICE_RESTORE,
    SERVICE_SNAPSHOT,
)
from .coordinator import XantechCoordinator
from .dax88_network import Dax88NetworkAmp
from .failover import FailoverAmp
from .network import (
    connection_mode,
    factory_reset_enabled,
    is_fallback,
    uses_fallback_port,
    uses_network,
    uses_serial,
)
from .port import port_label

if TYPE_CHECKING:
    from pyxantech import AmpControlBase

LOG = logging.getLogger(__name__)

type XantechConfigEntry = ConfigEntry[XantechData]


class XantechData:
    """Runtime data for Xantech integration."""

    def __init__(
        self,
        coordinator: XantechCoordinator,
        amp: AmpControlBase,
        amp_type: str,
        sources: dict[int, str],
        enable_audio_controls: bool = False,
        network_amp: Any = None,
        mode: str = 'serial',
    ) -> None:
        """Initialize runtime data."""
        self.coordinator = coordinator
        self.amp = amp
        # the amplifier's network (Linkplay) client when this entry has one. In a
        # fallback mode it is wrapped so its calls never overlap with the other link.
        self.network_amp = network_amp
        self.amp_type = amp_type
        self.sources = sources
        self.enable_audio_controls = enable_audio_controls
        self.mode = mode


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Xantech component (YAML config not supported)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: XantechConfigEntry) -> bool:
    """Set up Xantech Multi-Zone Amplifier from a config entry."""
    mode = connection_mode(dict(entry.data))
    network = uses_network(mode)
    port = entry.data.get(CONF_PORT)
    host = entry.data.get(CONF_HOST)
    # the raw port can carry an ESPHome API key, so only log/raise its label
    where = host if mode.startswith('network') else port_label(port or '')
    amp_type = entry.data[CONF_AMP_TYPE]
    zones_config = entry.data.get(CONF_ZONES, {})
    sources_config = entry.data.get(CONF_SOURCES, {})

    # convert zones config to list of zone IDs
    zone_ids = [int(zone_id) for zone_id in zones_config]

    # convert sources config to simple id->name mapping
    sources: dict[int, str] = {}
    for source_id, source_data in sources_config.items():
        sources[int(source_id)] = source_data.get('name', f'Source {source_id}')

    # get scan interval from options, with fallback to default
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    # make sure the library accepts the amp's full tone range
    apply_library_limit_overrides(amp_type)

    if network and amp_type != AMP_TYPE_DAX88:
        # the network module always uses the DAX88 value ranges (bass/treble 0-24);
        # an amp that has this module is a DAX88-family model
        LOG.warning(
            "The amplifier's network module uses the DAX88 value ranges, but this "
            'entry is set up as %s. If tone values look wrong on one of the links, '
            'set the amplifier up as Dayton Audio DAX88.',
            amp_type,
        )

    errors: list[str] = []

    async def _serial(url: str, *, quiet: bool = False) -> Any:
        """An RS-232 link: a serial port or RS-232 over IP (socket://host:port)."""
        shown = port_label(url)
        if quiet and not await async_socket_reachable(url):
            # a reopen while Home Assistant is running: opening socket:// blocks for
            # seconds when the bridge is down, so check first without blocking
            return None
        try:
            client = await async_get_amp_controller(amp_type, url, hass.loop)
            if not client and not quiet:
                errors.append(f'no serial controller for {amp_type} at {shown}')
            return client
        except (OSError, TimeoutError, SerialException) as err:
            # serialx raises OSError/TimeoutError as well as its own exception
            if not quiet:
                errors.append(
                    f'Serial connection error to {amp_type} at {shown}: {err}'
                )
        except Exception as err:  # noqa: BLE001
            LOG.debug('Serial setup failed for %s', shown, exc_info=True)
            if not quiet:
                errors.append(f'Failed to initialize {amp_type} at {shown}: {err}')
        return None

    serial_amp: Any = await _serial(port) if uses_serial(mode) and port else None
    backup_port = entry.data.get(CONF_FALLBACK_PORT)
    backup_amp: Any = None
    if uses_fallback_port(mode) and backup_port:
        backup_amp = await _serial(backup_port)
    network_client: Dax88NetworkAmp | None = None
    if network and host:
        network_client = Dax88NetworkAmp(
            host,
            entry.data.get(CONF_NETWORK_PORT, DEFAULT_NETWORK_PORT),
            # the kill switch: the client refuses a factory reset unless it is on
            allow_factory_reset=factory_reset_enabled(dict(entry.options)),
        )

    # what the settings features (names, backup, factory reset) talk to
    settings_client: Any = network_client
    if is_fallback(mode):
        clients = {
            'serial': serial_amp,
            'network': network_client,
            'serial_backup': backup_amp,
        }
        preferred, fallback = {
            'serial_fallback_network': ('serial', 'network'),
            'network_fallback_serial': ('network', 'serial'),
            'serial_fallback_serial': ('serial', 'serial_backup'),
        }[mode]
        if not (clients[preferred] or clients[fallback]):
            raise ConfigEntryNotReady('; '.join(errors) or 'no connection configured')
        # serial links can be opened again later: a port that was missing at start-up,
        # or whose adapter was unplugged, only works again as a new connection
        factories = {}
        if port and 'serial' in (preferred, fallback):
            factories['serial'] = lambda: _serial(port, quiet=True)
        if backup_port and 'serial_backup' in (preferred, fallback):
            factories['serial_backup'] = lambda: _serial(backup_port, quiet=True)
        amp = FailoverAmp(
            (preferred, clients[preferred]),
            (fallback, clients[fallback]),
            close_client=async_close_amp,
            factories=factories,
        )
        settings_client = amp.locked('network')
    else:
        amp = network_client if network else serial_amp
        if amp is None:
            raise ConfigEntryNotReady(
                '; '.join(errors) or f'Failed to connect to {amp_type} at {where}'
            )

    LOG.info('Set up %s amplifier (%s) at %s', amp_type, mode, where)

    # entity unique ids are built from this name, so it is fixed on first setup and
    # never recomputed: switching connection mode or a new IP keeps every entity id
    amp_name = entry.data.get(CONF_AMP_NAME)
    if not amp_name:
        if mode == MODE_NETWORK:
            amp_name = f'{amp_type}_net_{entry.data.get(CONF_DEVICE_ID, host)}'
        else:
            # the label, as upstream does: an ESPHome proxy URL (and its key)
            # must not end up in entity unique ids
            amp_name = f'{amp_type}_{port_label(port or "")}'.replace('/', '_')
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_AMP_NAME: amp_name}
        )
    coordinator = XantechCoordinator(
        hass,
        amp,
        amp_name,
        zone_ids,
        scan_interval,
        restorable=restorable_settings(amp_type),
    )

    # fetch initial data; release the port if the amp doesn't answer, otherwise
    # every setup retry would leave another open connection behind
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        async_close_amp(amp)
        raise

    # get feature settings
    enable_audio_controls = entry.data.get(CONF_ENABLE_AUDIO_CONTROLS, False)

    # store runtime data
    entry.runtime_data = XantechData(
        coordinator=coordinator,
        amp=amp,
        amp_type=amp_type,
        sources=sources,
        enable_audio_controls=enable_audio_controls,
        network_amp=settings_client,
        mode=mode,
    )

    # register services
    await _async_register_services(hass)

    # forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if settings_client is not None:
        # before the update listener is added: dropping an expired kill switch here
        # must not cause a reload
        _async_track_factory_reset_switch(hass, entry)

    # register update listener for options
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    if settings_client is not None:
        entry.async_create_background_task(
            hass,
            _async_show_firmware(hass, entry, settings_client, amp_name),
            f'{DOMAIN}_firmware_{entry.entry_id}',
        )

    return True


async def _async_show_firmware(
    hass: HomeAssistant, entry: XantechConfigEntry, client: Any, amp_name: str
) -> None:
    """Read the firmware over the network module and show it on the device page.

    Runs in the background so a network module that isn't answering (it may only be the
    fallback link) never delays start-up.
    """
    try:
        firmware = await client.firmware()
    except Exception:  # noqa: BLE001 - the link is checked again when it is needed
        LOG.warning(
            "The network module of %s isn't answering yet; names, backup and "
            'factory reset need it',
            entry.title,
        )
        return
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if (DOMAIN, amp_name) in device.identifiers and device.sw_version != firmware:
            registry.async_update_device(device.id, sw_version=firmware)


def _async_track_factory_reset_switch(
    hass: HomeAssistant, entry: XantechConfigEntry
) -> None:
    """Show a notice while the kill switch is on, and turn it off when it expires."""
    until = float(entry.options.get(CONF_FACTORY_RESET_ENABLED_UNTIL, 0))
    remaining = until - time.time()
    if remaining <= 0:
        settings.async_disable_factory_reset(hass, entry)
        return
    settings.async_show_switch_notice(hass, entry, remaining)

    @callback
    def _expire(_now: Any) -> None:
        if settings.reset_running(entry):
            # turning the switch off reloads the entry: not under a reset that has
            # already been confirmed and is waiting for the amplifier. It turns the
            # switch off itself when it ends.
            entry.async_on_unload(async_call_later(hass, 30, _expire))
            return
        settings.async_disable_factory_reset(hass, entry)

    entry.async_on_unload(async_call_later(hass, remaining, _expire))


async def async_unload_entry(hass: HomeAssistant, entry: XantechConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # close the serial port so a reload doesn't leave two readers on it
        async_close_amp(entry.runtime_data.amp)

    if unload_ok and not hass.config_entries.async_entries(DOMAIN):
        # cleanup services if no more entries
        for service in (
            SERVICE_SNAPSHOT,
            SERVICE_RESTORE,
            SERVICE_EXPORT_SETTINGS,
            SERVICE_IMPORT_SETTINGS,
            SERVICE_FACTORY_RESET,
        ):
            hass.services.async_remove(DOMAIN, service)

    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: XantechConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services."""
    if hass.services.has_service(DOMAIN, SERVICE_SNAPSHOT):
        return  # services already registered

    async def async_snapshot_service(call: ServiceCall) -> None:
        """Handle snapshot service call."""
        entity_ids = call.data.get(ATTR_ENTITY_ID, [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]

        entity_registry = er.async_get(hass)

        for entity_id in entity_ids:
            if hass.states.get(entity_id) and entity_registry.async_get(entity_id):
                LOG.debug('Snapshot requested for %s', entity_id)
                # dispatch to entity's snapshot method via event
                hass.bus.async_fire(
                    f'{DOMAIN}_snapshot',
                    {'entity_id': entity_id},
                )

    async def async_restore_service(call: ServiceCall) -> None:
        """Handle restore service call."""
        entity_ids = call.data.get(ATTR_ENTITY_ID, [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]

        for entity_id in entity_ids:
            if hass.states.get(entity_id):
                LOG.debug('Restore requested for %s', entity_id)
                hass.bus.async_fire(
                    f'{DOMAIN}_restore',
                    {'entity_id': entity_id},
                )

    hass.services.async_register(DOMAIN, SERVICE_SNAPSHOT, async_snapshot_service)
    hass.services.async_register(DOMAIN, SERVICE_RESTORE, async_restore_service)
    _async_register_settings_services(hass)


# --- network module actions: export / import / factory reset ---

ATTR_CONFIG_ENTRY_ID = 'config_entry_id'
ATTR_BACKUP_FILE = 'backup_file'


def _async_register_settings_services(hass: HomeAssistant) -> None:
    """The same checks as the options flow: both go through the settings module."""

    async def export_settings(call: ServiceCall) -> ServiceResponse:
        entry, _ = settings.network_amp(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        return {'backup_file': await settings.async_export(hass, entry)}

    async def import_settings(call: ServiceCall) -> ServiceResponse:
        entry, _ = settings.network_amp(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        left_alone = await settings.async_import(
            hass,
            entry,
            call.data[ATTR_BACKUP_FILE],
            include_power=call.data['include_power'],
        )
        # zones on now and off in the backup: their volume and mute were not changed
        return {'zones_left_alone': left_alone}

    async def factory_reset(call: ServiceCall) -> ServiceResponse:
        entry, _ = settings.network_amp(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        backup_name = await settings.async_factory_reset(
            hass, entry, confirm=call.data['confirm']
        )
        return {'backup_file': backup_name}

    entry_schema = {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_SETTINGS,
        export_settings,
        schema=vol.Schema(entry_schema),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_SETTINGS,
        import_settings,
        schema=vol.Schema(
            {
                **entry_schema,
                vol.Required(ATTR_BACKUP_FILE): cv.string,
                vol.Optional('include_power', default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FACTORY_RESET,
        factory_reset,
        schema=vol.Schema(
            {
                **entry_schema,
                # a new backup of this amplifier is always saved first: there is no
                # way to skip it or to pass another file instead
                vol.Required('confirm'): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
