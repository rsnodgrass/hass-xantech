"""Diagnostics support for Xantech Multi-Zone Amplifier."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_PORT, CONF_SOURCES, CONF_ZONES
from .failover import describe
from .network import connection_mode
from .port import port_label

if TYPE_CHECKING:
    from . import XantechConfigEntry

REDACTED = '[REDACTED]'
# keys to redact from diagnostics
REDACT_KEYS = {'serial_number', 'password', 'token', 'api_key'}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: XantechConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator

    # get zone statuses
    zone_data = {}
    if coordinator.data:
        for zone_id, status in coordinator.data.items():
            zone_data[str(zone_id)] = async_redact_data(status, REDACT_KEYS)

    # Which zones and inputs are set up, not what they are called. A diagnostics
    # file is made to be passed on, and the names are the user's own (rooms,
    # devices) or were read from the amplifier.
    zone_names = {str(zone_id): REDACTED for zone_id in entry.data.get(CONF_ZONES, {})}
    source_names = {
        str(source_id): REDACTED for source_id in entry.data.get(CONF_SOURCES, {})
    }

    return {
        'config_entry': {
            'entry_id': entry.entry_id,
            'version': entry.version,
            'domain': entry.domain,
            # holds the name the amplifier reported (by default with part of its
            # MAC address), or the address of a socket:// port
            'title': REDACTED,
            'data': {
                'port': _redact_port(entry.data.get(CONF_PORT, '')),
                'amp_type': entry.data.get('amp_type'),
                'connection_mode': connection_mode(dict(entry.data)),
                'host': '[REDACTED]' if entry.data.get(CONF_HOST) else None,
            },
            'connection': describe(entry.runtime_data.amp),
            'options': dict(entry.options),
        },
        'coordinator': {
            'name': coordinator.name,
            'update_interval_seconds': (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            'last_update_success': coordinator.last_update_success,
            'zone_ids': coordinator.zone_ids,
        },
        'zone_names': zone_names,
        'source_names': source_names,
        'zone_statuses': zone_data,
    }


def _redact_port(port: str) -> str:
    """Redact IP addresses from port string but keep device paths."""
    if port.startswith('socket://'):
        # redact IP but keep format visible
        return 'socket://[REDACTED]:[PORT]/'
    # device paths like /dev/ttyUSB0 are generally safe; serial proxy URLs
    # are reduced to the port name and ESPHome credentials are dropped
    return port_label(port)
