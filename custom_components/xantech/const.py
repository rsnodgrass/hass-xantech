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

# Transport (how Home Assistant talks to the amp)
CONF_TRANSPORT: Final = 'transport'
TRANSPORT_SERIAL: Final = 'serial'
TRANSPORT_NETWORK: Final = 'network'  # DAX88 only: Linkplay TCP passthrough
CONF_HOST: Final = 'host'
CONF_NETWORK_PORT: Final = 'network_port'
CONF_DEVICE_ID: Final = 'device_id'  # Linkplay module uuid: stable across IP changes
# a second RS-232 link (serial port or socket://host:port) used as the fallback
CONF_FALLBACK_PORT: Final = 'fallback_port'
# the name entity unique ids are built from: fixed when the entry is first set up,
# so switching connection (or a new IP) never changes entity ids
CONF_AMP_NAME: Final = 'amp_name'
# the device name shown in Home Assistant, when the amplifier told us its own name
CONF_DEVICE_LABEL: Final = 'device_label'

# Connection modes: one link only, or a preferred link with automatic fallback.
# 'serial' = RS-232, by cable or over IP (socket://host:port: Xantech MRIP, ser2net, ESP
# bridges); 'network' = the amplifier's built-in Linkplay Wi-Fi/LAN module (DAX88
# protocol). Every amplifier type can use them: the link is checked, not the model.
CONF_CONNECTION_MODE: Final = 'connection_mode'
MODE_SERIAL: Final = 'serial'
MODE_NETWORK: Final = 'network'
MODE_SERIAL_FALLBACK_NETWORK: Final = 'serial_fallback_network'
MODE_NETWORK_FALLBACK_SERIAL: Final = 'network_fallback_serial'
MODE_SERIAL_FALLBACK_SERIAL: Final = 'serial_fallback_serial'
CONNECTION_MODES: Final[list[str]] = [
    MODE_SERIAL,
    MODE_NETWORK,
    MODE_SERIAL_FALLBACK_NETWORK,
    MODE_NETWORK_FALLBACK_SERIAL,
    MODE_SERIAL_FALLBACK_SERIAL,
]
DEFAULT_NETWORK_PORT: Final = 8899
DAX88_LINKPLAY_PROJECT: Final = 'DAYTONAUDIO_DAX88'
# Linkplay module 'project' ids known to run the DAX88 control protocol. Others are
# accepted too when they answer it (checked on manual setup), so rebadged hardware
# works; add ids here when confirmed so discovery finds them as well.
KNOWN_LINKPLAY_PROJECTS: Final = frozenset({DAX88_LINKPLAY_PROJECT})

# Factory reset kill switch: an option (never an entity), off by default,
# and it expires by itself
CONF_FACTORY_RESET_ENABLED_UNTIL: Final = 'factory_reset_enabled_until'
FACTORY_RESET_ENABLE_MINUTES: Final = 30
BACKUP_DIR: Final = 'xantech_backups'

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

# Sonance6 volume range: below 40 is inaudibly quiet, physical max is 60
SONANCE6_MIN_VOLUME: Final = 40
SONANCE6_MAX_VOLUME: Final = 60

# Sonance6 EQ ranges (signed)
SONANCE6_MIN_BASS: Final = -8
SONANCE6_MAX_BASS: Final = 8
SONANCE6_MIN_TREBLE: Final = -8
SONANCE6_MAX_TREBLE: Final = 8
SONANCE6_MIN_BALANCE: Final = -10
SONANCE6_MAX_BALANCE: Final = 10

SUPPORTED_AMP_TYPES: Final[list[str]] = [
    AMP_TYPE_XANTECH8,
    AMP_TYPE_MONOPRICE6,
    AMP_TYPE_DAX88,
    AMP_TYPE_ZPR68,
    AMP_TYPE_SONANCE6,
]

# What the device page shows per amplifier series: (manufacturer, model)
DEVICE_MODELS: Final[dict[str, tuple[str, str]]] = {
    AMP_TYPE_XANTECH8: ('Xantech', '8-zone amplifier (MRC88, MX88, MRAUDIO8X8)'),
    AMP_TYPE_MONOPRICE6: (
        'Monoprice / Dayton Audio',
        '6-zone amplifier (MPR-SG6Z, DAX66)',
    ),
    AMP_TYPE_DAX88: ('Dayton Audio', 'DAX88'),
    AMP_TYPE_ZPR68: ('Xantech', 'ZPR68-10'),
    AMP_TYPE_SONANCE6: ('Sonance', 'C4630 SE / 875D'),
}

# Max volume level for amps
MAX_VOLUME: Final = 38

# Audio control fallback defaults (actual values loaded from pyxantech device config)
DEFAULT_MAX_BASS: Final = 14
DEFAULT_MAX_TREBLE: Final = 14
DEFAULT_MAX_BALANCE: Final = 20

# Service names
SERVICE_SNAPSHOT: Final = 'snapshot'
SERVICE_RESTORE: Final = 'restore'
SERVICE_EXPORT_SETTINGS: Final = 'export_settings'
SERVICE_IMPORT_SETTINGS: Final = 'import_settings'
SERVICE_FACTORY_RESET: Final = 'factory_reset'
FACTORY_RESET_PHRASE: Final = 'FACTORY RESET'

# Attributes
ATTR_ZONE_ID: Final = 'zone_id'
ATTR_SOURCE_ID: Final = 'source_id'

# Platforms
PLATFORMS: Final[list[str]] = ['media_player', 'number', 'sensor']
