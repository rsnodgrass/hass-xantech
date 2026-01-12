"""Number entities for Xantech Multi-Zone Amplifier audio controls."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENABLE_AUDIO_CONTROLS,
    CONF_ZONES,
    DOMAIN,
    MAX_BALANCE,
    MAX_BASS,
    MAX_TREBLE,
)
from .coordinator import XantechCoordinator

if TYPE_CHECKING:
    from . import XantechConfigEntry

LOG = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XantechConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Xantech number entities from a config entry."""
    # only create audio control entities if enabled
    if not entry.data.get(CONF_ENABLE_AUDIO_CONTROLS, False):
        LOG.debug('Audio controls disabled, skipping number entity setup')
        return

    data = entry.runtime_data
    coordinator = data.coordinator

    zones_config = entry.data.get(CONF_ZONES, {})

    entities: list[ZoneAudioControlNumber] = []

    for zone_id_str, zone_data in zones_config.items():
        zone_id = int(zone_id_str)
        zone_name = zone_data.get('name', f'Zone {zone_id}')

        # create bass, treble, balance entities for each zone
        entities.append(
            ZoneBassNumber(
                coordinator=coordinator,
                entry=entry,
                zone_id=zone_id,
                zone_name=zone_name,
            )
        )
        entities.append(
            ZoneTrebleNumber(
                coordinator=coordinator,
                entry=entry,
                zone_id=zone_id,
                zone_name=zone_name,
            )
        )
        entities.append(
            ZoneBalanceNumber(
                coordinator=coordinator,
                entry=entry,
                zone_id=zone_id,
                zone_name=zone_name,
            )
        )

    LOG.info(
        'Adding %d audio control entities for %s',
        len(entities),
        coordinator.amp_name,
    )
    async_add_entities(entities)


class ZoneAudioControlNumber(CoordinatorEntity[XantechCoordinator], NumberEntity):
    """Base class for zone audio control number entities."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: XantechCoordinator,
        entry: XantechConfigEntry,
        zone_id: int,
        zone_name: str,
        control_key: str,
        name_suffix: str,
    ) -> None:
        """Initialize the audio control number entity."""
        super().__init__(coordinator)

        self._zone_id = zone_id
        self._zone_name = zone_name
        self._control_key = control_key
        self._entry = entry

        # optimistic state tracking
        self._optimistic_value: int | None = None
        self._pending_commands: int = 0

        # entity attributes
        self._attr_unique_id = (
            f'{DOMAIN}_{coordinator.amp_name}_zone_{zone_id}_{control_key}'.lower()
            .replace(' ', '_')
        )
        self._attr_name = f'{zone_name} {name_suffix}'
        self._attr_translation_key = control_key

        # device info - link to same device as media player
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f'{coordinator.amp_name}')},
        )

    @property
    def _zone_status(self) -> dict[str, Any]:
        """Get current zone status."""
        if self.coordinator.data:
            return self.coordinator.data.get(self._zone_id, {})
        return {}

    def _set_optimistic(self, value: int) -> None:
        """Set optimistic value and trigger UI update."""
        self._optimistic_value = value
        self._pending_commands += 1
        self.async_write_ha_state()

    def _command_complete(self) -> None:
        """Mark a command as complete."""
        self._pending_commands = max(0, self._pending_commands - 1)

    def _clear_optimistic(self) -> None:
        """Clear optimistic state only when no commands are pending."""
        if self._pending_commands == 0:
            self._optimistic_value = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._clear_optimistic()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        if self._optimistic_value is not None:
            return float(self._optimistic_value)
        value = self._zone_status.get(self._control_key)
        if value is not None:
            return float(value)
        return None


class ZoneBassNumber(ZoneAudioControlNumber):
    """Number entity for zone bass control."""

    _attr_native_min_value = 0
    _attr_native_max_value = MAX_BASS
    _attr_native_step = 1
    _attr_icon = 'mdi:speaker-wireless'

    def __init__(
        self,
        coordinator: XantechCoordinator,
        entry: XantechConfigEntry,
        zone_id: int,
        zone_name: str,
    ) -> None:
        """Initialize the bass control entity."""
        super().__init__(
            coordinator=coordinator,
            entry=entry,
            zone_id=zone_id,
            zone_name=zone_name,
            control_key='bass',
            name_suffix='Bass',
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the bass level."""
        bass_value = int(value)
        LOG.debug('Setting bass=%d for zone %d', bass_value, self._zone_id)
        self._set_optimistic(bass_value)
        try:
            await self.coordinator.async_set_zone_bass(self._zone_id, bass_value)
        finally:
            self._command_complete()


class ZoneTrebleNumber(ZoneAudioControlNumber):
    """Number entity for zone treble control."""

    _attr_native_min_value = 0
    _attr_native_max_value = MAX_TREBLE
    _attr_native_step = 1
    _attr_icon = 'mdi:sine-wave'

    def __init__(
        self,
        coordinator: XantechCoordinator,
        entry: XantechConfigEntry,
        zone_id: int,
        zone_name: str,
    ) -> None:
        """Initialize the treble control entity."""
        super().__init__(
            coordinator=coordinator,
            entry=entry,
            zone_id=zone_id,
            zone_name=zone_name,
            control_key='treble',
            name_suffix='Treble',
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the treble level."""
        treble_value = int(value)
        LOG.debug('Setting treble=%d for zone %d', treble_value, self._zone_id)
        self._set_optimistic(treble_value)
        try:
            await self.coordinator.async_set_zone_treble(self._zone_id, treble_value)
        finally:
            self._command_complete()


class ZoneBalanceNumber(ZoneAudioControlNumber):
    """Number entity for zone balance control."""

    _attr_native_min_value = 0
    _attr_native_max_value = MAX_BALANCE
    _attr_native_step = 1
    _attr_icon = 'mdi:speaker-multiple'

    def __init__(
        self,
        coordinator: XantechCoordinator,
        entry: XantechConfigEntry,
        zone_id: int,
        zone_name: str,
    ) -> None:
        """Initialize the balance control entity."""
        super().__init__(
            coordinator=coordinator,
            entry=entry,
            zone_id=zone_id,
            zone_name=zone_name,
            control_key='balance',
            name_suffix='Balance',
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the balance level."""
        balance_value = int(value)
        LOG.debug('Setting balance=%d for zone %d', balance_value, self._zone_id)
        self._set_optimistic(balance_value)
        try:
            await self.coordinator.async_set_zone_balance(self._zone_id, balance_value)
        finally:
            self._command_complete()
