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
CONF_ENABLE_AUDIO_CONTROLS: Final = 'enable_audio_controls'

# Defaults
DEFAULT_NAME: Final = 'Xantech Multi-Zone Audio'
DEFAULT_AMP_TYPE: Final = 'xantech8'
DEFAULT_SCAN_INTERVAL: Final = 30

# Amplifier types supported by pyxantech
# xantech8: MX88, MX88ai, MRC88, MRC88m, MRAUDIO8X8, MRAUDIO8X8m
AMP_TYPE_XANTECH8: Final = 'xantech8'
# monoprice6: Monoprice MPR-SG6Z, Dayton Audio DAX66, Soundavo WS66i
AMP_TYPE_MONOPRICE6: Final = 'monoprice6'
# dax88: Dayton Audio DAX88 (6+2 zone)
AMP_TYPE_DAX88: Final = 'dax88'
# zpr68-10: Xantech ZPR68-10 controller
AMP_TYPE_ZPR68: Final = 'zpr68-10'
# sonance6: Sonance C4630 SE (6-zone), 875D MKII (4-zone)
AMP_TYPE_SONANCE6: Final = 'sonance6'

SUPPORTED_AMP_TYPES: Final[list[str]] = [
    AMP_TYPE_XANTECH8,
    AMP_TYPE_MONOPRICE6,
    AMP_TYPE_DAX88,
    AMP_TYPE_ZPR68,
    AMP_TYPE_SONANCE6,
]

# Max volume level for amps
MAX_VOLUME: Final = 38

# Audio control fallback defaults (actual values loaded from pyxantech device config)
DEFAULT_MAX_BASS: Final = 14
DEFAULT_MAX_TREBLE: Final = 14
DEFAULT_MAX_BALANCE: Final = 20

# Service names
SERVICE_SNAPSHOT: Final = 'snapshot'
SERVICE_RESTORE: Final = 'restore'

# Attributes
ATTR_ZONE_ID: Final = 'zone_id'
ATTR_SOURCE_ID: Final = 'source_id'

# Platforms
PLATFORMS: Final[list[str]] = ['media_player', 'number']
