"""Number entities for Xantech Multi-Zone Amplifier audio controls."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyxantech import get_device_config

from .audio_controls import (
    CONTROL_BALANCE,
    CONTROL_BASS,
    CONTROL_TREBLE,
    ControlRange,
    get_control_range,
)
from .const import CONF_ENABLE_AUDIO_CONTROLS, CONF_ZONES, DOMAIN
from .coordinator import XantechCoordinator

if TYPE_CHECKING:
    from . import XantechConfigEntry

LOG = logging.getLogger(__name__)

# control key -> (name suffix, icon)
_CONTROLS: dict[str, tuple[str, str]] = {
    CONTROL_BASS: ('Bass', 'mdi:speaker-wireless'),
    CONTROL_TREBLE: ('Treble', 'mdi:sine-wave'),
    CONTROL_BALANCE: ('Balance', 'mdi:speaker-multiple'),
}


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
    amp_type = data.amp_type

    # check device capabilities for tone controls
    supported = [
        control
        for control in _CONTROLS
        if get_device_config(amp_type, f'supports_{control}', log_missing=False)
    ]
    if not supported:
        LOG.debug('Device %s does not support any tone controls', amp_type)
        return

    ranges = {control: get_control_range(amp_type, control) for control in supported}
    zones_config = entry.data.get(CONF_ZONES, {})

    entities: list[ZoneAudioControlNumber] = []
    for zone_id_str, zone_data in zones_config.items():
        zone_id = int(zone_id_str)
        zone_name = zone_data.get('name', f'Zone {zone_id}')
        entities.extend(
            ZoneAudioControlNumber(
                coordinator=coordinator,
                entry=entry,
                zone_id=zone_id,
                zone_name=zone_name,
                control_key=control,
                control_range=ranges[control],
            )
            for control in supported
        )

    LOG.info(
        'Adding %d audio control entities for %s',
        len(entities),
        coordinator.amp_name,
    )
    async_add_entities(entities)


class ZoneAudioControlNumber(CoordinatorEntity[XantechCoordinator], NumberEntity):
    """Bass, treble or balance control for one zone.

    Values are shown relative to the neutral point where it is known
    (e.g. DAX88 bass -12..+12 dB, 0 = flat) and converted to the raw amp
    value when sent.
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: XantechCoordinator,
        entry: XantechConfigEntry,
        zone_id: int,
        zone_name: str,
        control_key: str,
        control_range: ControlRange,
    ) -> None:
        """Initialize the audio control number entity."""
        super().__init__(coordinator)

        name_suffix, icon = _CONTROLS[control_key]
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._control_key = control_key
        self._range = control_range
        self._entry = entry

        self._attr_native_min_value = float(control_range.native_min)
        self._attr_native_max_value = float(control_range.native_max)
        self._attr_native_unit_of_measurement = control_range.unit
        self._attr_icon = icon

        # optimistic state tracking (raw amp value)
        self._optimistic_raw: int | None = None
        self._pending_commands: int = 0

        # entity attributes - unique_id format must stay stable
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

    @property
    def available(self) -> bool:
        """Available only while the amp answers for this zone."""
        return super().available and bool(self._zone_status)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # not on a poll that commands overtook: it is older than the value set last
        if self._pending_commands == 0 and not self.coordinator.zone_overtaken(
            self._zone_id
        ):
            self._optimistic_raw = None
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        raw = self._optimistic_raw
        if raw is None:
            raw = self._zone_status.get(self._control_key)
        if raw is None:
            return None
        return float(self._range.from_raw(int(raw)))

    def _setter(self) -> Callable[[int, int], Awaitable[None]]:
        return {
            CONTROL_BASS: self.coordinator.async_set_zone_bass,
            CONTROL_TREBLE: self.coordinator.async_set_zone_treble,
            CONTROL_BALANCE: self.coordinator.async_set_zone_balance,
        }[self._control_key]

    async def async_set_native_value(self, value: float) -> None:
        """Set the control, converting to the raw amp value."""
        raw = self._range.to_raw(value)
        LOG.debug(
            'Setting %s=%s (raw %d) for zone %d',
            self._control_key,
            value,
            raw,
            self._zone_id,
        )
        self._optimistic_raw = raw
        self._pending_commands += 1
        self.async_write_ha_state()
        try:
            await self._setter()(self._zone_id, raw)
        except BaseException:
            # It did not reach the amplifier (an error, or it was cancelled): stop
            # showing it and ask the amplifier what it has. Not while a later
            # command is still on its way: its value is newer.
            if self._pending_commands <= 1:
                self._optimistic_raw = None
                if self.hass is not None:
                    self.async_write_ha_state()
                self.coordinator.hass.async_create_task(
                    self.coordinator.async_request_refresh()
                )
            raise
        finally:
            self._pending_commands = max(0, self._pending_commands - 1)
