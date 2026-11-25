"""Diagnostic sensors for the amplifier."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_NETWORK
from .coordinator import XantechCoordinator

if TYPE_CHECKING:
    from . import XantechConfigEntry

LINKS = ['serial', 'network', 'serial_backup']


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XantechConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the diagnostic sensors for an amplifier."""
    async_add_entities([ConnectionSensor(entry.runtime_data.coordinator, entry)])


class ConnectionSensor(CoordinatorEntity[XantechCoordinator], SensorEntity):
    """Which link to the amplifier is in use right now.

    With a fallback connection mode this shows when Home Assistant has switched to the
    other link. It stays available while the amplifier is unreachable, because that is
    exactly when it is worth looking at.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = LINKS
    _attr_translation_key = 'connection'

    def __init__(
        self, coordinator: XantechCoordinator, entry: XantechConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._mode = entry.runtime_data.mode
        self._attr_unique_id = (
            f'{DOMAIN}_{coordinator.amp_name}_connection'.lower().replace(' ', '_')
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f'{coordinator.amp_name}')},
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        active = self.coordinator.active_link
        if active in LINKS:
            return active
        return 'network' if self._mode == MODE_NETWORK else 'serial'

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            'connection_mode': self._mode,
            'amplifier_answering': self.coordinator.last_update_success,
        }
