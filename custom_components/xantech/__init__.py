"""Xantech Multi-Zone Amplifier Control for Home Assistant."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
from pyxantech import async_get_amp_controller
from serial import SerialException

from .const import (
    CONF_AMP_TYPE,
    CONF_ENABLE_AUDIO_CONTROLS,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SOURCES,
    CONF_ZONES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_RESTORE,
    SERVICE_SNAPSHOT,
)
from .coordinator import XantechCoordinator

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
        sources: dict[int, str],
        enable_audio_controls: bool = False,
    ) -> None:
        """Initialize runtime data."""
        self.coordinator = coordinator
        self.amp = amp
        self.sources = sources
        self.enable_audio_controls = enable_audio_controls


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Xantech component (YAML config not supported)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: XantechConfigEntry) -> bool:
    """Set up Xantech Multi-Zone Amplifier from a config entry."""
    port = entry.data[CONF_PORT]
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

    try:
        amp = await async_get_amp_controller(amp_type, port, hass.loop)
        if not amp:
            raise ConfigEntryNotReady(f'Failed to connect to {amp_type} at {port}')
    except SerialException as err:
        raise ConfigEntryNotReady(
            f'Serial connection error to {amp_type} at {port}: {err}'
        ) from err
    except Exception as err:
        LOG.exception('Failed to initialize amplifier')
        raise ConfigEntryNotReady(f'Failed to initialize {amp_type} at {port}') from err

    LOG.info('Connected to %s amplifier at %s', amp_type, port)

    # create coordinator
    amp_name = f'{amp_type}_{port}'.replace('/', '_')
    coordinator = XantechCoordinator(
        hass,
        amp,
        amp_name,
        zone_ids,
        scan_interval,
    )

    # fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # get feature settings
    enable_audio_controls = entry.data.get(CONF_ENABLE_AUDIO_CONTROLS, False)

    # store runtime data
    entry.runtime_data = XantechData(
        coordinator=coordinator,
        amp=amp,
        sources=sources,
        enable_audio_controls=enable_audio_controls,
    )

    # register services
    await _async_register_services(hass)

    # forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # register update listener for options
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: XantechConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and not hass.config_entries.async_entries(DOMAIN):
        # cleanup services if no more entries
        for service in (SERVICE_SNAPSHOT, SERVICE_RESTORE):
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
