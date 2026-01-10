"""Constants for the Xantech Multi-Zone Amplifier integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = 'xantech'

# Configuration keys
CONF_PORT: Final = 'port'
CONF_AMP_TYPE: Final = 'amp_type'
CONF_ZONES: Final = 'zones'
CONF_SOURCES: Final = 'sources'
CONF_SERIAL_CONFIG: Final = 'rs232'
CONF_ZONE_NAME: Final = 'name'
CONF_DEFAULT_SOURCE: Final = 'default_source'

# Options
CONF_SCAN_INTERVAL: Final = 'scan_interval'

# Defaults
DEFAULT_NAME: Final = 'Xantech Multi-Zone Audio'
DEFAULT_AMP_TYPE: Final = 'xantech8'
DEFAULT_SCAN_INTERVAL: Final = 30

# Amplifier types supported by pyxantech
AMP_TYPE_XANTECH8: Final = 'xantech8'
AMP_TYPE_MONOPRICE6: Final = 'monoprice6'
AMP_TYPE_DAX88: Final = 'dax88'
AMP_TYPE_ZPR68: Final = 'zpr68-10'
AMP_TYPE_SONANCE: Final = 'sonance'

SUPPORTED_AMP_TYPES: Final[list[str]] = [
    AMP_TYPE_XANTECH8,
    AMP_TYPE_MONOPRICE6,
    AMP_TYPE_DAX88,
    AMP_TYPE_ZPR68,
    AMP_TYPE_SONANCE,
]

# Max volume level for amps
MAX_VOLUME: Final = 38

# Service names
SERVICE_SNAPSHOT: Final = 'snapshot'
SERVICE_RESTORE: Final = 'restore'

# Attributes
ATTR_ZONE_ID: Final = 'zone_id'
ATTR_SOURCE_ID: Final = 'source_id'

# Platforms
PLATFORMS: Final[list[str]] = ['media_player']
