"""DataUpdateCoordinator for Xantech Multi-Zone Amplifier."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

if TYPE_CHECKING:
    from pyxantech import AmpControlBase

LOG = logging.getLogger(__name__)


class XantechCoordinator(DataUpdateCoordinator[dict[int, dict[str, Any]]]):
    """Coordinator to manage fetching zone statuses from the amplifier."""

    def __init__(
        self,
        hass: HomeAssistant,
        amp: AmpControlBase,
        amp_name: str,
        zone_ids: list[int],
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance
            amp: The pyxantech amplifier controller
            amp_name: Friendly name of the amplifier
            zone_ids: List of zone IDs to poll
            scan_interval: Polling interval in seconds
        """
        super().__init__(
            hass,
            LOG,
            name=f'{DOMAIN}_{amp_name}',
            update_interval=timedelta(seconds=scan_interval),
        )
        self.amp = amp
        self.amp_name = amp_name
        self.zone_ids = zone_ids
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5

    async def _async_update_data(self) -> dict[int, dict[str, Any]]:
        """Fetch data from the amplifier for all zones.

        Returns:
            Dictionary mapping zone_id to zone status dict
        """
        zone_statuses: dict[int, dict[str, Any]] = {}

        try:
            for zone_id in self.zone_ids:
                try:
                    status = await self.amp.zone_status(zone_id)
                    if status:
                        zone_statuses[zone_id] = status
                    else:
                        LOG.debug('No status returned for zone %d', zone_id)
                except Exception:
                    LOG.warning(
                        'Failed to get status for zone %d', zone_id, exc_info=True
                    )
                    # continue with other zones even if one fails

            # reset error counter on success
            self._consecutive_errors = 0

            LOG.debug('Updated %d zones for %s', len(zone_statuses), self.amp_name)
            return zone_statuses

        except Exception as err:
            self._consecutive_errors += 1
            if self._consecutive_errors >= self._max_consecutive_errors:
                LOG.error(
                    'Failed to update %s after %d attempts',
                    self.amp_name,
                    self._consecutive_errors,
                    exc_info=err,
                )
            raise UpdateFailed(
                f'Error communicating with {self.amp_name}: {err}'
            ) from err

    async def async_set_zone_power(self, zone_id: int, power: bool) -> None:
        """Set power state for a zone."""
        try:
            await self.amp.set_power(zone_id, power)
            await self.async_request_refresh()
        except Exception:
            LOG.exception('Failed to set power for zone %d', zone_id)
            raise

    async def async_set_zone_source(self, zone_id: int, source_id: int) -> None:
        """Set source for a zone."""
        try:
            await self.amp.set_source(zone_id, source_id)
            await self.async_request_refresh()
        except Exception:
            LOG.exception('Failed to set source for zone %d', zone_id)
            raise

    async def async_set_zone_volume(self, zone_id: int, volume: int) -> None:
        """Set volume for a zone (0-38 scale)."""
        try:
            await self.amp.set_volume(zone_id, volume)
            await self.async_request_refresh()
        except Exception:
            LOG.exception('Failed to set volume for zone %d', zone_id)
            raise

    async def async_set_zone_mute(self, zone_id: int, mute: bool) -> None:
        """Set mute state for a zone."""
        try:
            await self.amp.set_mute(zone_id, mute)
            await self.async_request_refresh()
        except Exception:
            LOG.exception('Failed to set mute for zone %d', zone_id)
            raise

    async def async_get_zone_snapshot(self, zone_id: int) -> dict[str, Any] | None:
        """Get a snapshot of zone status for later restoration."""
        try:
            return await self.amp.zone_status(zone_id)
        except Exception:
            LOG.exception('Failed to snapshot zone %d', zone_id)
            raise

    async def async_restore_zone(self, snapshot: dict[str, Any]) -> None:
        """Restore a zone from a snapshot."""
        try:
            await self.amp.restore_zone(snapshot)
            await self.async_request_refresh()
        except Exception:
            LOG.exception('Failed to restore zone')
            raise
