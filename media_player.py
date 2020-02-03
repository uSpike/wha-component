import asyncio
import itertools
import logging

import voluptuous as vol

from homeassistant.const import (
    CONF_NAME,
    CONF_ENTITY_ID,
    STATE_IDLE,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.components.media_player import PLATFORM_SCHEMA, MediaPlayerDevice
from homeassistant.components.media_player.const import (
    DOMAIN,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_VOLUME_SET,
    SUPPORT_VOLUME_STEP,
)
from homeassistant.core import callback
from homeassistant.helpers import collection
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity

_LOGGER = logging.getLogger('wha.media_player')

DOMAIN = "media_player"

CONF_SPEAKERS = "speakers"
CONF_SNAPCLIENT = "snapclient"
CONF_RECEIVER = "receiver"
CONF_MIN = "min"
CONF_MAX = "max"
CONF_DEFAULT = "default"
CONF_VOLUME = "volume"

volume_int = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))

SPEAKER_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): cv.string,
    vol.Optional('on_default', default=True): cv.boolean,
    vol.Optional(CONF_SNAPCLIENT): {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Optional('volume'): {
            vol.Optional(CONF_MIN): volume_int,
            vol.Optional(CONF_MAX): volume_int,
            vol.Optional(CONF_DEFAULT): volume_int,
        },
    },
    vol.Optional(CONF_RECEIVER): {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required('source'): cv.string,
        vol.Optional('volume'): {
            vol.Optional(CONF_MIN): volume_int,
            vol.Optional(CONF_MAX): volume_int,
            vol.Optional(CONF_DEFAULT): volume_int,
        },
    },
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required('snapgroup'): cv.entity_id,
    vol.Required(CONF_SPEAKERS): [SPEAKER_SCHEMA],
})


class WHAStorageCollection(collection.StorageCollection):
    pass


async def async_setup_platform(hass, config, add_entities, discovery_info=None):
    speakers = [
        Speaker(hass, conf)
        for conf in config.get(CONF_SPEAKERS)
    ]
    group = Group(
        hass,
        config.get(CONF_NAME),
        config.get("snapgroup"),
        speakers,
    )
    for speaker in speakers:
        speaker._group = group
    add_entities([group] + speakers)


class Speaker(MediaPlayerDevice, RestoreEntity):
    def __init__(self, hass, config):
        self._hass = hass
        self._group = None
        self._state = STATE_OFF
        self._name = config.get(CONF_NAME)
        self._snap = Wrapped(hass, config[CONF_SNAPCLIENT])
        if CONF_RECEIVER in config:
            self._receiver = Wrapped(hass, config[CONF_RECEIVER])
        else:
            self._receiver = None
        self._on_default = config.get('on_default')
        self._volume_cfg = config.get('volume')

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._state = last_state.state

        @callback
        def async_on_dependency_update(*_):
            self.async_schedule_update_ha_state(True)

        self.hass.helpers.event.async_track_state_change(
            self.entity_ids, async_on_dependency_update
        )

    @property
    def should_poll(self):
        return False

    @property
    def state(self):
        return self._state

    @property
    def volume_level(self):
        if self._receiver:
            return self._receiver.volume_level
        else:
            return self._snap.volume_level

    @property
    def name(self):
        return self._name

    @property
    def is_volume_muted(self):
        if self._receiver:
            return self._receiver.attrs.get("is_volume_muted")
        else:
            return self._snap.attrs.get("is_volume_muted")

    @property
    def source(self):
        return self._snap.attrs.get('source')

    @property
    def source_list(self):
        return self._snap.attrs.get('source_list')

    @property
    def _min_volume(self):
        return self.volume_cfg.get(CONF_MIN, 0)

    @property
    def _max_volume(self):
        return self.volume_cfg.get(CONF_MAX, 100)

    @property
    def entity_ids(self):
        return [
            self._snap.entity_id,
            *(self._receiver and [self._receiver.entity_id] or []),
        ]

    @property
    def supported_features(self):
        return (
            SUPPORT_TURN_ON
            | SUPPORT_TURN_OFF
            | SUPPORT_VOLUME_MUTE
            | SUPPORT_VOLUME_SET
            | SUPPORT_VOLUME_STEP
#            | SUPPORT_SELECT_SOURCE
        )

    async def async_turn_on(self, group=False):
        if self._receiver:
            await self._receiver.turn_on()
            await self._receiver.set_default_volume()
            await self._receiver.select_source(self._receiver.source)
            await self._snap.turn_on()
            await self._snap.set_volume_level(1)
        else:
            await self._snap.turn_on()
            await self._snap.set_default_volume()
        self._state = STATE_ON
        await self.async_mute_volume(False)

    async def async_turn_off(self, group=False):
        if self._receiver:
            await self._receiver.select_default_source()
            await self._receiver.turn_off()
            await self._snap.turn_off()
        else:
            await self._snap.turn_off()
        self._state = STATE_OFF
        await self.async_mute_volume(True)

#    async def async_select_source(self, source):
#        await self._snap.select_source(source)

    async def async_mute_volume(self, mute):
        data = {"is_volume_muted": mute}
        if self._receiver:
            await self._receiver.call_service("volume_mute", data)
        await self._snap.call_service("volume_mute", data)

    async def async_set_volume_level(self, volume):
        if self._receiver:
            await self._receiver.set_volume_level(volume)
        else:
            await self._snap.set_volume_level(volume)



class Wrapped:
    def __init__(self, hass, config):
        self.hass = hass
        self.entity_id = config.get(CONF_ENTITY_ID)
        self.source = config.get('source')
        volume_cfg = config.get('volume', {})
        self.min_volume = volume_cfg.get(CONF_MIN, 0) / 100
        self.max_volume = volume_cfg.get(CONF_MAX, 100) / 100
        self.default_volume = volume_cfg.get(CONF_DEFAULT)
        self.default_source = None

    @property
    def state(self):
        return self.hass.states.get(self.entity_id)

    @property
    def attrs(self):
        return getattr(self.state, 'attributes', {})

    @property
    def volume_scale(self):
        return self.max_volume - self.min_volume

    def get_volume_level(self, volume):
        return self.min_volume + (self.volume_scale * volume)

    @property
    def volume_level(self):
        volume = self.attrs.get("volume_level", 0)
        return self.get_volume_level(volume)

    async def call_service(self, name, data):
        if self.entity_id:
            data["entity_id"] = self.entity_id
            await self.hass.services.async_call(DOMAIN, name, data)

    async def turn_on(self):
        await self.call_service("turn_on", {})
        self.default_source = self.attrs.get('source')

    async def turn_off(self):
        await self.select_default_source()
        await self.call_service("turn_off", {})

    async def mute_volume(self, mute):
        await self.call_service("volume_mute", {"is_volume_muted": mute})

    async def set_volume_level(self, volume):
        data = {"volume_level": self.get_volume_level(volume)}
        await self.call_service("volume_set", data)

    async def set_default_volume(self):
        if self.default_volume is not None:
            data = {"volume_level": self.default_volume / 100}
            await self.call_service("volume_set", data)

    async def select_source(self, source):
        await self.call_service("select_source", {"source": source})

    async def select_default_source(self):
        if self.default_source is not None:
            await self.select_source(self.default_source)


class Group(MediaPlayerDevice, RestoreEntity):

    def __init__(self, hass, name, group, speakers):
        """Initialize the Universal media device."""
        self.hass = hass
        self._name = name
        self._speakers = speakers
        self._snap_entity = group
        self._state = STATE_OFF

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._state = last_state.state

        @callback
        def async_on_dependency_update(*_):
            self.async_schedule_update_ha_state(True)

        entities = [self._snap_entity]
        self.hass.helpers.event.async_track_state_change(
            entities, async_on_dependency_update
        )

    @property
    def _snap_state(self):
        return self.hass.states.get(self._snap_entity)

    @property
    def _snap_attr(self):
        return getattr(self._snap_state, 'attributes', {})

    @property
    def should_poll(self):
        return False

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def source(self):
        return self._snap_attr.get('source')

    @property
    def source_list(self):
        return self._snap_attr.get('source_list')

    @property
    def volume_level(self):
        return sum(s.volume_level for s in self._speakers) / len(self._speakers)

    @property
    def is_volume_muted(self):
        return self._snap_attr.get('is_volume_muted')

    @property
    def supported_features(self):
        return (
            SUPPORT_TURN_ON
            | SUPPORT_TURN_OFF
            | SUPPORT_VOLUME_MUTE
            | SUPPORT_VOLUME_STEP
            | SUPPORT_SELECT_SOURCE
        )

    async def _async_call_snap_service(self, name, data):
        data["entity_id"] = self._snap_entity
        await self.hass.services.async_call(DOMAIN, name, data)

    async def async_turn_on(self):
        for speaker in self._speakers:
            if speaker._on_default:
                data = {'entity_id': speaker.entity_id}
                await self.hass.services.async_call(DOMAIN, "turn_on", data)
        self._state = STATE_ON
        self.async_schedule_update_ha_state(True)

    async def async_turn_off(self):
        for speaker in self._speakers:
            if speaker._on_default:
                data = {'entity_id': speaker.entity_id}
                await self.hass.services.async_call(DOMAIN, "turn_off", data)
        self._state = STATE_OFF
        self.async_schedule_update_ha_state(True)

    async def async_mute_volume(self, mute):
        await self._async_call_snap_service("volume_mute", {"is_volume_muted": mute})

    async def async_volume_up(self):
        for speaker in self._speakers:
            await speaker.async_volume_up()

    async def async_volume_down(self):
        for speaker in self._speakers:
            await speaker.async_volume_down()

    async def async_select_source(self, source):
        await self._async_call_snap_service("select_source", {"source": source})
