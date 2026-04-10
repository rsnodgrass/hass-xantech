"""Diagnostics support for Xantech Multi-Zone Amplifier."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_PORT, CONF_SOURCES, CONF_ZONES

if TYPE_CHECKING:
    from . import XantechConfigEntry

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

    # get zone configuration with names
    zones_config = entry.data.get(CONF_ZONES, {})
    zone_names = {
        str(zone_id): zone_info.get('name', f'Zone {zone_id}')
        for zone_id, zone_info in zones_config.items()
    }

    # get sources configuration
    sources_config = entry.data.get(CONF_SOURCES, {})
    source_names = {
        str(source_id): source_info.get('name', f'Source {source_id}')
        for source_id, source_info in sources_config.items()
    }

    return {
        'config_entry': {
            'entry_id': entry.entry_id,
            'version': entry.version,
            'domain': entry.domain,
            'title': entry.title,
            'data': {
                'port': _redact_port(entry.data.get(CONF_PORT, '')),
                'amp_type': entry.data.get('amp_type'),
            },
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
    # device paths like /dev/ttyUSB0 are generally safe
    return port
