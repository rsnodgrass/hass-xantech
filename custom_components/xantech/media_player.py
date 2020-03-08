"""Home Assistant Media Player for Xantech, Monoprice and Dayton Audio multi-zone amplifiers""" 

import logging

import voluptuous as vol
from serial import SerialException
from pyxantech import get_amp_controller, SUPPORTED_AMP_TYPES, BAUD_RATES

from homeassistant.components.media_player import PLATFORM_SCHEMA, MediaPlayerDevice
from homeassistant.components.media_player.const import (
    SUPPORT_SELECT_SOURCE,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_VOLUME_SET,
    SUPPORT_VOLUME_STEP,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_NAME,
    CONF_PORT,
    CONF_TYPE,
    STATE_OFF,
    STATE_ON,
)
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, SERVICE_RESTORE, SERVICE_SNAPSHOT

LOG = logging.getLogger(__name__)

DATA_AMP_GLOBAL = "xantech_monoprice"

SUPPORTED_AMP_FEATURES = (
    SUPPORT_VOLUME_MUTE
    | SUPPORT_VOLUME_SET
    | SUPPORT_VOLUME_STEP
    | SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_SELECT_SOURCE
)

CONF_SOURCES = "sources"
CONF_ZONES = "zones"
CONF_BAUDRATE = "baudrate"
CONF_DEFAULT_SOURCE = "default_source"

# Valid source ids: 
#    monoprice6: 1-6 (Monoprice and Dayton Audio)
#    xantech8:   1-8
SOURCE_IDS = vol.All(vol.Coerce(int), vol.Range(min=1, max=8))
SOURCE_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME, default="Unknown Source"): cv.string}
)

# TODO: this should come from config for each model...from underlying pyxantech
#
# Valid zone ids: 
#   monoprice6: 11-16 or 21-26 or 31-36 (Monoprice and Dayton Audio)
#   xantech8:   11-18 or 21-28 or 31-38 or 1-8
ZONE_IDS = vol.All(
    vol.Coerce(int),
    vol.Any(
        vol.Range(min=1, max=8),
        vol.Range(min=11, max=18),
        vol.Range(min=21, max=28),
        vol.Range(min=31, max=38)
    ),
)
ZONE_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME, default="Audio Zone"): cv.string,
#    vol.Optional(CONF_DEFAULT_SOURCE): vol.In(SOURCE_IDS)
    vol.Optional(CONF_DEFAULT_SOURCE): cv.positive_int
})

# FIXME: is this necessary?
MEDIA_PLAYER_SCHEMA = vol.Schema({ATTR_ENTITY_ID: cv.comp_entity_ids})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_TYPE, default="xantech8"): vol.In(SUPPORTED_AMP_TYPES),
        vol.Required(CONF_PORT): cv.string,
        vol.Required(CONF_ZONES): vol.Schema({ZONE_IDS: ZONE_SCHEMA}),
        vol.Required(CONF_SOURCES): vol.Schema({SOURCE_IDS: SOURCE_SCHEMA}),
        vol.Optional(CONF_BAUDRATE, default=9600): vol.In(BAUD_RATES)
    }
)

MAX_VOLUME = 38

def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Xantech 8-zone amplifier platform."""
    port = config.get(CONF_PORT)
    amp_type = config.get(CONF_TYPE)

    try:
        serial_config = {
            'rs232': { 'baudrate': config.get(CONF_BAUDRATE) }
        }
        amp = get_amp_controller(amp_type, port, serial_config)
    except SerialException:
        LOG.error("Error connecting to '%s' amplifier using %s", amp_type, port)
        return

    sources = {
        source_id: extra[CONF_NAME] for source_id, extra in config[CONF_SOURCES].items()
    }

    hass.data[DATA_AMP_GLOBAL] = []
    for zone_id, extra in config[CONF_ZONES].items():
        LOG.info("Adding %s zone %d - %s", amp_type, zone_id, extra[CONF_NAME])
        amp_zone = AmpZone(amp, sources, zone_id, extra[CONF_NAME])
        hass.data[DATA_AMP_GLOBAL].append(amp_zone)

    add_entities(hass.data[DATA_AMP_GLOBAL], True)

    def service_handle(service):
        """Handle for services."""
        entity_ids = service.data.get(ATTR_ENTITY_ID)

        if entity_ids:
            devices = [
                device
                for device in hass.data[DATA_AMP_GLOBAL]
                if device.entity_id in entity_ids
            ]
        else:
            devices = hass.data[DATA_AMP_GLOBAL]

        for device in devices:
            if service.service == SERVICE_SNAPSHOT:
                device.snapshot()
            elif service.service == SERVICE_RESTORE:
                device.restore()

    # register the save/restore snapshot service APIs
    hass.services.register(DOMAIN, SERVICE_SNAPSHOT, service_handle, schema=MEDIA_PLAYER_SCHEMA)
    hass.services.register(DOMAIN, SERVICE_RESTORE, service_handle, schema=MEDIA_PLAYER_SCHEMA)


class AmpZone(MediaPlayerDevice):
    """Representation of a matrix amplifier zone."""

    def __init__(self, amp, sources, zone_id, zone_name):
        """Initialize new zone."""
        self._amp = amp
        self._zone_id = zone_id        
        self._name = zone_name

        self._status = None
        self._status_snapshot = None

        # dict source_id -> source name
        self._source_id_to_name = sources

        # dict source name -> source_id
        self._source_name_to_id = {v: k for k, v in sources.items()}

        # FIXME: should this be sorted, or an order specified in HA config?
        # ordered list of all source names
        self._source_names = sorted(
            self._source_name_to_id.keys(), key=lambda v: self._source_name_to_id[v]
        )

    def update(self):
        """Retrieve latest state."""
        status = self._amp.zone_status(self._zone_id)
        if not status:
            return False

        # FIXME: check that status has the required fields before replacing existing

        LOG.debug(f"{self._name} zone {self._zone_id} status update: {status}")
        self._status = status

        source_id = status['source']
        if source_id in self._source_id_to_name:
            self._source = self._source_id_to_name[source_id]
        else:
            self._source = None

        return True

    @property
    def name(self):
        """Return the name of the zone."""
        return self._name

    @property
    def state(self):
        """Return the powered on state of the zone."""
        power = self._status['power']
        return power is not None and power == True

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        volume = self._status['volume']
        if volume is None:
            return None
        return volume / MAX_VOLUME

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        # FIXME: what about when volume == 0?
        return self._status['mute']

    @property
    def supported_features(self):
        """Return flag of media commands that are supported."""
        return SUPPORTED_AMP_FEATURES

    @property
    def media_title(self):
        """Return the current source as medial title."""
        return self._source

    @property
    def source(self):
        """Return the current input source of the device."""
        return self._source

    @property
    def source_list(self):
        """List of available input sources."""
        return self._source_names

    def snapshot(self):
        """Save zone's current state."""
        self._status_snapshot = self._amp.zone_status(self._zone_id)

    def restore(self):
        """Restore saved state."""
        if self._status_snapshot:
            self._amp.restore_zone(self._status_snapshot)
            self.schedule_update_ha_state(True)

    def select_source(self, source):
        """Set input source."""
        LOG.info(f"Selecting {source} in {self._source_name_to_id}")
        if source not in self._source_name_to_id:
            return

        source_id = self._source_name_to_id[source]
        LOG.info(f"Switching zone {self._zone_id} to source {source_id} ({source})")
        self._amp.set_source(self._zone_id, source_id)

    def turn_on(self):
        """Turn the media player on."""
        LOG.debug(f"Turning {self._name} zone {self._zone_id} on")
        self._amp.set_power(self._zone_id, True)

    def turn_off(self):
        """Turn the media player off."""
        LOG.debug(f"Turning {self._name} zone {self._zone_id} off")
        self._amp.set_power(self._zone_id, False)

    def mute_volume(self, mute):
        """Mute (true) or unmute (false) media player."""
        self._amp.set_mute(self._zone_id, mute)

    def set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        amp_volume = int(volume / MAX_VOLUME)
        LOG.warning(f"Would have set {self._name} zone {self._zone_id} volume to {amp_volume} (HA volume {volume}")
        # self._amp.set_volume(self._zone_id, amp_volume)
        # FIXME

    def volume_up(self):
        """Volume up the media player."""
        volume = self._status['volume']
        if volume is None:
            return

        # reminder the volume is on the amplifier scale (0-38), not Home Assistants (1-100)
        self._amp.set_volume(self._zone_id, min(volume + 1, MAX_VOLUME))

    def volume_down(self):
        """Volume down media player."""
        volume = self._status['volume']
        if volume is None:
            return

        # reminder the volume is on the amplifier scale (0-38), not Home Assistants (1-100)
        self._amp.set_volume(self._zone_id, max(volume - 1, 0))
