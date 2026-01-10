"""Media Player entities for Xantech Multi-Zone Amplifier."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ZONES,
    DOMAIN,
    MAX_VOLUME,
)
from .coordinator import XantechCoordinator

if TYPE_CHECKING:
    from . import XantechConfigEntry

LOG = logging.getLogger(__name__)

SUPPORTED_ZONE_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.SELECT_SOURCE
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XantechConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Xantech media player entities from a config entry."""
    data = entry.runtime_data
    coordinator = data.coordinator
    sources = data.sources

    zones_config = entry.data.get(CONF_ZONES, {})

    entities: list[ZoneMediaPlayer] = []

    for zone_id_str, zone_data in zones_config.items():
        zone_id = int(zone_id_str)
        zone_name = zone_data.get('name', f'Zone {zone_id}')

        entities.append(
            ZoneMediaPlayer(
                coordinator=coordinator,
                entry=entry,
                zone_id=zone_id,
                zone_name=zone_name,
                sources=sources,
            )
        )

    LOG.info('Adding %d zone media players for %s', len(entities), coordinator.amp_name)
    async_add_entities(entities)


class ZoneMediaPlayer(CoordinatorEntity[XantechCoordinator], MediaPlayerEntity):
    """Representation of a matrix amplifier zone."""

    _attr_has_entity_name = True
    _attr_supported_features = SUPPORTED_ZONE_FEATURES

    def __init__(
        self,
        coordinator: XantechCoordinator,
        entry: XantechConfigEntry,
        zone_id: int,
        zone_name: str,
        sources: dict[int, str],
    ) -> None:
        """Initialize the zone media player."""
        super().__init__(coordinator)

        self._zone_id = zone_id
        self._zone_name = zone_name

        # source mappings
        self._source_id_to_name = sources
        self._source_name_to_id: dict[str, int] = {v: k for k, v in sources.items()}
        self._source_names = sorted(
            self._source_name_to_id.keys(),
            key=lambda v: self._source_name_to_id[v],
        )

        # snapshot storage
        self._status_snapshot: dict[str, Any] | None = None

        # entity attributes - preserve existing unique_id format for migration
        self._attr_unique_id = (
            f'{DOMAIN}_{coordinator.amp_name}_zone_{zone_id}'.lower().replace(' ', '_')
        )
        self._attr_name = zone_name

        # device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f'{coordinator.amp_name}')},
            name=f'Xantech {coordinator.amp_name}',
            manufacturer='Xantech',
            model='Multi-Zone Amplifier',
        )

        self._entry = entry

    @property
    def _zone_status(self) -> dict[str, Any]:
        """Get current zone status from coordinator data."""
        if self.coordinator.data:
            return self.coordinator.data.get(self._zone_id, {})
        return {}

    @property
    def state(self) -> MediaPlayerState:
        """Return the powered on state of the zone."""
        power = self._zone_status.get('power')
        if power is True:
            return MediaPlayerState.ON
        return MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        volume = self._zone_status.get('volume')
        if volume is None:
            return None
        return volume / MAX_VOLUME

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        return self._zone_status.get('mute', False)

    @property
    def source(self) -> str | None:
        """Return the current input source of the device."""
        source_id = self._zone_status.get('source')
        if source_id is None:
            return None

        source_name = self._source_id_to_name.get(source_id)
        if source_name:
            return source_name

        # dynamically create source if amp reports unknown source
        source_name = f'Source {source_id}'
        LOG.debug('Unknown source id %d, using %s', source_id, source_name)
        return source_name

    @property
    def source_list(self) -> list[str]:
        """List of available input sources."""
        return self._source_names

    @property
    def icon(self) -> str:
        """Return the icon for this zone."""
        if self.state == MediaPlayerState.OFF or self.is_volume_muted:
            return 'mdi:speaker-off'
        return 'mdi:speaker'

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        LOG.debug('Turning on zone %d', self._zone_id)
        await self.coordinator.async_set_zone_power(self._zone_id, True)

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        LOG.debug('Turning off zone %d', self._zone_id)
        await self.coordinator.async_set_zone_power(self._zone_id, False)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute media player."""
        LOG.debug('Setting mute=%s for zone %d', mute, self._zone_id)
        await self.coordinator.async_set_zone_mute(self._zone_id, mute)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0-1.0."""
        amp_volume = int(volume * MAX_VOLUME)
        LOG.debug('Setting zone %d volume to %d', self._zone_id, amp_volume)
        await self.coordinator.async_set_zone_volume(self._zone_id, amp_volume)

    async def async_volume_up(self) -> None:
        """Volume up the media player."""
        volume = self._zone_status.get('volume')
        if volume is None:
            return
        new_volume = min(volume + 1, MAX_VOLUME)
        await self.coordinator.async_set_zone_volume(self._zone_id, new_volume)

    async def async_volume_down(self) -> None:
        """Volume down media player."""
        volume = self._zone_status.get('volume')
        if volume is None:
            return
        new_volume = max(volume - 1, 0)
        await self.coordinator.async_set_zone_volume(self._zone_id, new_volume)

    async def async_select_source(self, source: str) -> None:
        """Set input source."""
        if source not in self._source_name_to_id:
            LOG.warning(
                'Source %s not valid for zone %d, available: %s',
                source,
                self._zone_id,
                list(self._source_name_to_id.keys()),
            )
            return

        source_id = self._source_name_to_id[source]
        LOG.debug(
            'Switching zone %d to source %d (%s)', self._zone_id, source_id, source
        )
        await self.coordinator.async_set_zone_source(self._zone_id, source_id)

    async def async_snapshot(self) -> None:
        """Save zone's current state."""
        self._status_snapshot = await self.coordinator.async_get_zone_snapshot(
            self._zone_id
        )
        LOG.info('Saved state snapshot for zone %d', self._zone_id)

    async def async_restore(self) -> None:
        """Restore saved state."""
        if self._status_snapshot:
            await self.coordinator.async_restore_zone(self._status_snapshot)
            LOG.info('Restored previous state for zone %d', self._zone_id)
        else:
            LOG.warning(
                'Restore called for zone %d, but no snapshot saved', self._zone_id
            )

    async def async_added_to_hass(self) -> None:
        """Register event listeners when entity is added."""
        await super().async_added_to_hass()

        # listen for snapshot/restore events
        self.async_on_remove(
            self.hass.bus.async_listen(
                f'{DOMAIN}_snapshot',
                self._handle_snapshot_event,
            )
        )
        self.async_on_remove(
            self.hass.bus.async_listen(
                f'{DOMAIN}_restore',
                self._handle_restore_event,
            )
        )

    @callback
    def _handle_snapshot_event(self, event: Event[dict[str, Any]]) -> None:
        """Handle snapshot event."""
        if event.data.get('entity_id') == self.entity_id:
            self.hass.async_create_task(self.async_snapshot())

    @callback
    def _handle_restore_event(self, event: Event[dict[str, Any]]) -> None:
        """Handle restore event."""
        if event.data.get('entity_id') == self.entity_id:
            self.hass.async_create_task(self.async_restore())
