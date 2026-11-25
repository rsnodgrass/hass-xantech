"""DataUpdateCoordinator for Xantech Multi-Zone Amplifier."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .audio_controls import SNAPSHOT_SETTINGS
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

if TYPE_CHECKING:
    from pyxantech import AmpControlBase

LOG = logging.getLogger(__name__)

# A poll reads every zone again when a command was sent while it was reading, this
# many times in all at most. Commands that keep coming (a slider being dragged) must
# not hold the poll for ever. A poll that runs out of passes says so (``overtaken``),
# and the refresh after the last command settles the state.
MAX_POLL_PASSES = 3


class XantechCoordinator(DataUpdateCoordinator[dict[int, dict[str, Any]]]):
    """Coordinator to manage fetching zone statuses from the amplifier."""

    def __init__(
        self,
        hass: HomeAssistant,
        amp: AmpControlBase,
        amp_name: str,
        zone_ids: list[int],
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        restorable: frozenset[str] | None = None,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance
            amp: The pyxantech amplifier controller
            amp_name: Friendly name of the amplifier
            zone_ids: List of zone IDs to poll
            scan_interval: Polling interval in seconds
            restorable: The settings a zone snapshot of this amplifier really
                holds (see audio_controls.restorable_settings); all by default
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
        self.restorable = SNAPSHOT_SETTINGS if restorable is None else restorable
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5
        # changes sent to the amplifier so far; see async_command_sent()
        self._commands_sent = 0
        # the zones a change was sent to during the pass of the poll that is running
        # (None stands for every zone: a restore of the amplifier's settings)
        self._pass_zones: set[int | None] = set()
        # True while the data comes from a poll that a command overtook on its last
        # pass too: for the zones in _overtaken_zones (None: every zone) it is older
        # than what the zone was last set to. See zone_overtaken().
        self.overtaken = False
        self._overtaken_zones: frozenset[int] | None = None

    async def _async_read_zones(self) -> dict[int, dict[str, Any]]:
        """Ask the amplifier for the status of every zone, one after the other."""
        zone_statuses: dict[int, dict[str, Any]] = {}
        for zone_id in self.zone_ids:
            try:
                status = await self.amp.zone_status(zone_id)
                if status:
                    zone_statuses[zone_id] = status
                else:
                    LOG.debug('No status returned for zone %d', zone_id)
            except Exception:
                LOG.warning('Failed to get status for zone %d', zone_id, exc_info=True)
                # continue with other zones even if one fails
        return zone_statuses

    async def _async_update_data(self) -> dict[int, dict[str, Any]]:
        """Fetch data from the amplifier for all zones.

        Returns:
            Dictionary mapping zone_id to zone status dict
        """
        zone_statuses: dict[int, dict[str, Any]] = {}

        try:
            for _ in range(MAX_POLL_PASSES):
                sent = self._commands_sent
                self._pass_zones = set()
                zone_statuses = await self._async_read_zones()
                self.overtaken = sent != self._commands_sent
                self._overtaken_zones = (
                    None
                    if None in self._pass_zones
                    else frozenset(z for z in self._pass_zones if z is not None)
                )
                if not self.overtaken:
                    break
                # A command was sent while the zones were being read, so some of
                # them were read before it. Reported like that, they would show the
                # old state until the refresh the command asked for, and that one
                # waits ten seconds because this poll is running.
                LOG.debug('A command was sent during the poll of %s', self.amp_name)

            if self.zone_ids and not zone_statuses:
                # nothing answered: port down, adapter unplugged or amp off.
                # Raising marks every entity unavailable instead of "off".
                raise UpdateFailed(
                    f'No response from {self.amp_name} for any zone'
                )

            # reset error counter on success
            self._consecutive_errors = 0

            LOG.debug('Updated %d zones for %s', len(zone_statuses), self.amp_name)
            return zone_statuses

        except UpdateFailed:
            self._consecutive_errors += 1
            raise
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

    def zone_overtaken(self, zone_id: int) -> bool:
        """True when the data for this zone may be older than what it was set to.

        That is so for the zones a command was sent to during the last pass of the
        poll. Every other zone was read after its last command, so its entity shows
        what the amplifier reported, also while another zone is being changed.
        """
        return self.overtaken and (
            self._overtaken_zones is None or zone_id in self._overtaken_zones
        )

    def _count_command(self, zone_id: int | None) -> None:
        self._commands_sent += 1
        self._pass_zones.add(zone_id)

    async def async_command_sent(self, zone_id: int | None = None) -> None:
        """Count a change that was sent to the amplifier and ask for a refresh.

        ``zone_id`` is the zone that was changed; None when it may be any of them.

        A poll that is running at this moment read some zones before the change. It
        holds the refresh lock, so the refresh asked for here waits for the end of
        Home Assistant's ten-second cooldown, and until then the poll's older answer
        would replace the new state. The count lets that poll see the change and
        read again (see _async_update_data).
        """
        self._count_command(zone_id)
        await self.async_request_refresh()

    async def async_set_zone_power(self, zone_id: int, power: bool) -> None:
        """Set power state for a zone."""
        try:
            await self.amp.set_power(zone_id, power)
            await self.async_command_sent(zone_id)
        except Exception:
            LOG.exception('Failed to set power for zone %d', zone_id)
            raise

    async def async_set_zone_source(self, zone_id: int, source_id: int) -> None:
        """Set source for a zone."""
        try:
            await self.amp.set_source(zone_id, source_id)
            await self.async_command_sent(zone_id)
        except Exception:
            LOG.exception('Failed to set source for zone %d', zone_id)
            raise

    async def async_set_zone_volume(self, zone_id: int, volume: int) -> None:
        """Set volume for a zone (0-38 scale)."""
        try:
            await self.amp.set_volume(zone_id, volume)
            await self.async_command_sent(zone_id)
        except Exception:
            LOG.exception('Failed to set volume for zone %d', zone_id)
            raise

    async def async_set_zone_mute(self, zone_id: int, mute: bool) -> None:
        """Set mute state for a zone."""
        try:
            await self.amp.set_mute(zone_id, mute)
            await self.async_command_sent(zone_id)
        except Exception:
            LOG.exception('Failed to set mute for zone %d', zone_id)
            raise

    async def async_set_zone_bass(self, zone_id: int, bass: int) -> None:
        """Set bass level for a zone (0-14, where 7 is neutral)."""
        try:
            await self.amp.set_bass(zone_id, bass)
            await self.async_command_sent(zone_id)
        except Exception:
            LOG.exception('Failed to set bass for zone %d', zone_id)
            raise

    async def async_set_zone_treble(self, zone_id: int, treble: int) -> None:
        """Set treble level for a zone (0-14, where 7 is neutral)."""
        try:
            await self.amp.set_treble(zone_id, treble)
            await self.async_command_sent(zone_id)
        except Exception:
            LOG.exception('Failed to set treble for zone %d', zone_id)
            raise

    async def async_set_zone_balance(self, zone_id: int, balance: int) -> None:
        """Set balance for a zone (0-20, where 10 is center)."""
        try:
            await self.amp.set_balance(zone_id, balance)
            await self.async_command_sent(zone_id)
        except Exception:
            LOG.exception('Failed to set balance for zone %d', zone_id)
            raise

    async def async_get_zone_snapshot(self, zone_id: int) -> dict[str, Any] | None:
        """Get a snapshot of zone status for later restoration."""
        try:
            return await self.amp.zone_status(zone_id)
        except Exception:
            LOG.exception('Failed to snapshot zone %d', zone_id)
            raise

    async def async_restore_zone(self, zone_id: int, snapshot: dict[str, Any]) -> None:
        """Put a zone back to a snapshot, one setting at a time.

        Done with the ordinary set commands, so it works on every amplifier type and
        over every connection. (The library's own restore does nothing for several
        amplifier series, and uses the zone number inside the snapshot, which is not
        always the zone id Home Assistant uses.)
        """
        settings = (
            ('source', self.amp.set_source),
            ('volume', self.amp.set_volume),
            ('bass', self.amp.set_bass),
            ('treble', self.amp.set_treble),
            ('balance', self.amp.set_balance),
            ('mute', self.amp.set_mute),
        )
        try:
            # a zone that should be off is switched off first; one that should be on
            # is switched on last, after its volume and input are back
            if snapshot.get('power') is False:
                await self.amp.set_power(zone_id, False)
            for key, setter in settings:
                # The serial library fills in a default for every setting the amp
                # did not report, so only the ones this amp type reports are real.
                if key in self.restorable and snapshot.get(key) is not None:
                    await setter(zone_id, snapshot[key])
            if snapshot.get('power') is True:
                await self.amp.set_power(zone_id, True)
            # At once, not async_request_refresh(): that one is held back for ten
            # seconds after the last command, and a restore usually follows some, so
            # the zone would keep showing what it had before the restore.
            self._count_command(zone_id)  # a poll that is running reads again
            await self.async_refresh()
        except Exception:
            LOG.exception('Failed to restore zone %d', zone_id)
            raise

    @property
    def active_link(self) -> str | None:
        """The link in use when the entry has a fallback ('serial', 'network', ...)."""
        return getattr(self.amp, 'active_transport', None)
