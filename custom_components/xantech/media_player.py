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
    CONF_ENTITY_NAMESPACE,
    CONF_NAME,
    CONF_PORT,
    CONF_TYPE,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.helpers import config_validation as cv, entity_platform, service

from .const import DOMAIN, SERVICE_RESTORE, SERVICE_SNAPSHOT

LOG = logging.getLogger(__name__)

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
CONF_DEFAULT_SOURCE = "default_source"
CONF_SERIAL_CONFIG = "rs232"

# Valid source ids: 
#    monoprice6: 1-6 (Monoprice and Dayton Audio)
#    xantech8:   1-8
SOURCE_IDS = vol.All(vol.Coerce(int), vol.Range(min=1, max=8))
SOURCE_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME, default="Unknown Source"): cv.string}
)

# TODO: this should come from config for each model...from underlying pyxantech, which
# probably requires checking at runtime (plus a failure in one zone id shouldn't fail
# ALL the zones from being created)
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
    vol.Optional(CONF_DEFAULT_SOURCE): cv.positive_int
})

SERIAL_CONFIG_SCHEMA = vol.Schema({
    vol.Optional("baudrate"): vol.In(BAUD_RATES)
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_TYPE, default="xantech8"): vol.In(SUPPORTED_AMP_TYPES),
        vol.Required(CONF_PORT): cv.string,
        vol.Optional(CONF_ENTITY_NAMESPACE, default="xantech8"): cv.string,
        vol.Required(CONF_ZONES): vol.Schema({ZONE_IDS: ZONE_SCHEMA}),
        vol.Required(CONF_SOURCES): vol.Schema({SOURCE_IDS: SOURCE_SCHEMA}),
        vol.Optional(CONF_SERIAL_CONFIG): SERIAL_CONFIG_SCHEMA
    }
)

# schema for media player service calls
SERVICE_CALL_SCHEMA = vol.Schema({ATTR_ENTITY_ID: cv.comp_entity_ids})

MAX_VOLUME = 38 # TODO: remove this and use pyxantech amp type configuration

def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Xantech amplifier platform."""
    port = config.get(CONF_PORT)
    amp_type = config.get(CONF_TYPE)
    namespace = config.get(CONF_ENTITY_NAMESPACE)

    try:
        # allow manually overriding any of the serial configuration using the 'rs232' key
        serial_config = config.get(CONF_SERIAL_CONFIG)
        if serial_config is None:
            serial_config = None
        
        amp = get_amp_controller(amp_type, port, serial_config)
    except SerialException:
        LOG.error(f"Error connecting to '{amp_type}' amp using {port}, ignoring setup!")
        return

    sources = {
        source_id: extra[CONF_NAME] for source_id, extra in config[CONF_SOURCES].items()
    }

    LOG.info(f"Creating media player for each zone of {amp_type}/{namespace}; sources={sources}")
    devices = []
    for zone_id, extra in config[CONF_ZONES].items():
        devices.append( ZoneMediaPlayer(namespace, amp, sources, zone_id, extra[CONF_NAME]) )
    add_entities(devices, True)

    platform = entity_platform.current_platform.get()

    def _service_call_dispatcher(entities, service_call):
        for entity in entities:
            if service_call.service == SERVICE_SNAPSHOT:
                entity.snapshot()
            elif service_call.service == SERVICE_RESTORE:
                entity.restore()

    # @service.verify_domain_control(hass, DOMAIN)
    def service_handle(service_call):
        entities = platform.extract_from_service(service_call)
        if not entities:
            return

        # FIXME: async version: hass.async_add_executor_job(_service_call_dispatcher, entities, service_call)
        _service_call_dispatcher(entities, service_call)

    # register the save/restore snapshot services
    for service_call in [ SERVICE_SNAPSHOT, SERVICE_RESTORE ]:
        hass.services.register(DOMAIN, service_call, service_handle, schema=SERVICE_CALL_SCHEMA)


class ZoneMediaPlayer(MediaPlayerDevice):
    """Representation of a matrix amplifier zone."""

    def __init__(self, namespace, amp, sources, zone_id, zone_name):
        """Initialize new zone."""
        LOG.info(f"Creating  {namespace} media player for zone {zone_id} ({zone_name})")

        self._amp = amp
        self._name = zone_name
        self._zone_id = zone_id        

        # FIXME: since this should be a logical media player...why is it not good enough for the user
        # specified name to represent this?  Other than it could be changed...
        self._unique_id = f"{namespace}_{zone_id}"

        self._status = {}
        self._status_snapshot = None
        
        self._source = None
        self._source_id_to_name = sources # [source_id] -> source name
        self._source_name_to_id = {v: k for k, v in sources.items()} # [source name] -> source_id

        # sort list of source names
        self._source_names = sorted(
            self._source_name_to_id.keys(), key=lambda v: self._source_name_to_id[v]
        )
        # TODO: ideally the source order could be overridden in YAML config (e.g. TV should appear first on list)

    def update(self):
        """Retrieve latest state."""
        try:
            LOG.debug(f"Attempting to update {self._zone_id} ({self._name})")
            status = self._amp.zone_status(self._zone_id)
            if not status:
                return
        except Exception as e:
            LOG.warning(f"Failed updating zone {self._zone_id} ({self._name}): %s", e)
            return

        LOG.debug(f"Zone {self._zone_id} ({self._name}) status update: {status}")
        self._status = status

        source_id = status.get('source')
        if source_id:
            source_name = self._source_id_to_name.get(source_id)
            if source_name: 
                self._source = source_name
            else:
                LOG.error(f"Invalid source id '{source_id}' specified for zone {self._zone_id} ({self._name}), ignoring!")

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the zone."""
        return self._name

    @property
    def state(self):
        """Return the powered on state of the zone."""
        power = self._status.get('power')
        if power is not None and power == True:
            return STATE_ON
        else:
            return STATE_OFF

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        volume = self._status.get('volume')
        if volume is None:
            return None
        return volume / MAX_VOLUME

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        # FIXME: what about when volume == 0?
        mute = self._status.get('mute')
        if mute is None:
            mute = False
        return mute

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
        LOG.info(f"Saved state snapshot for zone {self._zone_id} ({self._name})")

    def restore(self):
        """Restore saved state."""
        if self._status_snapshot:
            self._amp.restore_zone(self._status_snapshot)
            self.schedule_update_ha_state(True)
            LOG.info(f"Restored previous state for zone {self._zone_id} ({self._name})")
        else:
            LOG.warning(f"Restore service called for zone {self._zone_id} ({self._name}), but no snapshot previously saved.")

    def select_source(self, source):
        """Set input source."""
        if source not in self._source_name_to_id:
            LOG.warning(f"Selected source '{source}' not valid for zone {self._zone_id} ({self._name}), ignoring! Sources: {self._source_name_to_id}")
            return

        source_id = self._source_name_to_id[source]
        LOG.info(f"Switching zone {self._zone_id} ({self._name}) to source {source_id} ({source})")
        self._amp.set_source(self._zone_id, source_id)

    def turn_on(self):
        """Turn the media player on."""
        LOG.debug(f"Turning ON zone {self._zone_id} ({self._name})")
        self._amp.set_power(self._zone_id, True)

    def turn_off(self):
        """Turn the media player off."""
        LOG.debug(f"Turning OFF zone {self._zone_id} ({self._name}))")
        self._amp.set_power(self._zone_id, False)

    def mute_volume(self, mute):
        """Mute (true) or unmute (false) media player."""
        LOG.debug(f"Setting mute={mute} for zone {self._zone_id} ({self._name}))")
        self._amp.set_mute(self._zone_id, mute)

    def set_volume_level(self, volume):
        """Set volume level, range 0—1.0"""
        amp_volume = int(volume * MAX_VOLUME)
        LOG.debug(f"Setting zone {self._zone_id} ({self._name}) volume to {amp_volume} (HA volume {volume}")
        self._amp.set_volume(self._zone_id, amp_volume)

    def volume_up(self):
        """Volume up the media player."""
        volume = self._status.get('volume')
        if volume is None:
            return

        # FIXME: call the volume up API on the amp object, instead of manually increasing volume
        # reminder the volume is on the amplifier scale (0-38), not Home Assistants (1-100)
        self._amp.set_volume(self._zone_id, min(volume + 1, MAX_VOLUME))

    def volume_down(self):
        """Volume down media player."""
        volume = self._status.get('volume')
        if volume is None:
            return

        # FIXME: call the volume down API on the amp object, instead of manually increasing volume
        # reminder the volume is on the amplifier scale (0-38), not Home Assistants (1-100)
        self._amp.set_volume(self._zone_id, max(volume - 1, 0))

    def icon(self):
        if self.state == STATE_OFF or self.is_volume_muted:
            return 'mdi:speaker-off'
        else:
            return 'mdi:speaker'
