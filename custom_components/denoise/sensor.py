"""
Denoise sensor
"""
import datetime
import logging
import math
import numbers
from typing import Union, Optional, Dict, Any

import homeassistant.util.dt as dt_util
import voluptuous as vol
from homeassistant.components import history
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.group import expand_entity_ids
from homeassistant.components.history import LazyState
from homeassistant.components.water_heater import DOMAIN as WATER_HEATER_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.const import (
    CONF_NAME,
    CONF_ENTITY_ID,
    EVENT_HOMEASSISTANT_START,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
    ATTR_ICON,
    ATTR_DEVICE_CLASS,
    DEVICE_CLASS_TEMPERATURE,
)
from homeassistant.core import callback, split_entity_id
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change
from homeassistant.util.temperature import convert as convert_temperature
from homeassistant.util.unit_system import TEMPERATURE_UNITS

DEFAULT_NAME = "Denoise sensor"

CONF_TIME_DELTA = "time_delta"
CONF_VALUE_DELTA = "value_delta"
CONF_PRECISION = "precision"

class DOMAIN_TYPE:
    WEATHER = 1
    CLIMATE = 2
    OTHER = 3

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = vol.All(
    PLATFORM_SCHEMA.extend(
        {
            vol.Required(CONF_ENTITY_ID): cv.entity_id,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_TIME_DELTA): cv.time_period,
            vol.Optional(CONF_VALUE_DELTA, default=0): float,
            vol.Optional(CONF_PRECISION, default=1): int,
        }
    ),
)

# pylint: disable=unused-argument
async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up platform."""
    name = config.get(CONF_NAME)
    time_delta = config.get(CONF_TIME_DELTA)
    value_delta = config.get(CONF_VALUE_DELTA)
    entity_id = config.get(CONF_ENTITY_ID)
    precision = config.get(CONF_PRECISION)

    _LOGGER.info("Setup [%s] time_delta[%s] val_delta[%s] prec[%s]",
        name, time_delta, value_delta, precision)

    async_add_entities(
        [DenoiseSensor(hass, name, time_delta, value_delta, entity_id, precision)]
    )

# pylint: disable=r0902
class DenoiseSensor(Entity):
    """Implementation of the Denoise sensor."""

    # pylint: disable=r0913
    def __init__(
        self,
        hass,
        name,
        time_delta,
        value_delta,
        entity_id,
        precision,
    ):
        """Initialize the sensor."""
        self._hass = hass
        self._name = name
        self._time_delta = time_delta
        self._value_delta = value_delta
        self._src_entity_id = entity_id
        self._precision = precision
        self._state = None
        self._unit_of_measurement = None
        self._device_class = None
        self._icon = None
        self._temperature_mode = None
        self._last_value = None
        self._last_update = None
        self._updated = False

    @property
    def force_update(self) -> bool:
        """Return True if state updates should be forced.
        If True, a state change will be triggered anytime the state property is
        updated, not just when the value changes.
        """
        return self._updated

    @property
    def _has_time_delta(self) -> bool:
        """Return True if sensor has a time delta setting."""
        return self._time_delta is not None

    @property
    def should_poll(self) -> bool:
        """Return the polling state."""
        return self._has_time_delta

    @property
    def name(self) -> Optional[str]:
        """Return the name of the sensor."""
        return self._name

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._state is not None

    @property
    def state(self) -> Union[None, str, int, float]:
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self) -> Optional[str]:
        """Return the unit of measurement of this entity."""
        return self._unit_of_measurement

    @property
    def device_class(self) -> Optional[str]:
        """Return the device class of this entity."""
        return self._device_class

    @property
    def icon(self) -> Optional[str]:
        """Return the icon to use in the frontend."""
        return self._icon

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        # pylint: disable=unused-argument
        @callback
        def sensor_state_listener(entity, old_state, new_state):
            """Handle device state changes."""
            self._update_state()
            if self.force_update:
                self.async_schedule_update_ha_state(False)

        # pylint: disable=unused-argument
        @callback
        def sensor_startup(event):
            """Track changes and initial update"""
            async_track_state_change(
                self._hass, self._src_entity_id, sensor_state_listener
            )
            sensor_state_listener(None, None, None)

        self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, sensor_startup)

    def update(self):
        """Update the sensor state if it needed."""
        if self._has_time_delta:
            self._update_state(time_trigger=True)

    @staticmethod
    def _has_state(state) -> bool:
        """Return True if state has any value."""
        return state is not None and state not in [
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
            "None",
        ]

    def _init_entity(self, state: LazyState):
        """Init entity attributes"""
        # Assume a temperature entity
        self._temperature_mode = True
        self._device_class = DEVICE_CLASS_TEMPERATURE
        self._icon = "mdi:thermometer"
        self._unit_of_measurement = self._hass.config.units.temperature_unit
        self._src_uom = self._unit_of_measurement
        self._src_domain = split_entity_id(state.entity_id)[0]

        if self._src_domain == WEATHER_DOMAIN:
            self._src_domain_type = DOMAIN_TYPE.WEATHER
        elif self._src_domain in (CLIMATE_DOMAIN, WATER_HEATER_DOMAIN):
            self._src_domain_type = DOMAIN_TYPE.CLIMATE
        else:
            self._src_domain_type = DOMAIN_TYPE.OTHER
            self._src_uom = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            self._temperature_mode = self._src_uom in TEMPERATURE_UNITS
            if not self._temperature_mode:
                self._unit_of_measurement = self._src_uom
                self._device_class = state.attributes.get(ATTR_DEVICE_CLASS)
                self._icon = state.attributes.get(ATTR_ICON)

    def _get_temperature(self, state: LazyState) -> Optional[float]:
        """Get temperature value from entity."""
        if self._src_domain_type == DOMAIN_TYPE.WEATHER:
            temperature = state.attributes.get("temperature")
        elif self._src_domain_type == DOMAIN_TYPE.CLIMATE:
            temperature = state.attributes.get("current_temperature")
        else:
            temperature = state.state

        if not self._has_state(temperature):
            return None

        temperature = convert_temperature(float(temperature), self._src_uom, self._unit_of_measurement)
        return temperature

    def _get_state_value(self, state: LazyState) -> Optional[float]:
        """Return value of given entity state."""
        if self._temperature_mode:
            return self._get_temperature(state)

        state = state.state
        if not self._has_state(state):
            return None

        try:
            state = float(state)
        except ValueError:
            _LOGGER.error('Could not convert value "%s" to float', state)
            return None

        return state

    def _update_state(self, time_trigger=False):  # pylint: disable=r0914,r0912,r0915
        """Update the sensor state."""

        self._updated = False
        now_ts = dt_util.utcnow()

        update_time = (self._has_time_delta and (self._last_update is None or
            now_ts - self._last_update > self._time_delta))

        if time_trigger and not update_time:
            return

        state = self._hass.states.get(self._src_entity_id)  # type: LazyState
        if state is None:
            _LOGGER.error('Unable to find entity "%s"', self._src_entity_id)
            return

        # if not initialised
        if self._temperature_mode is None:
            self._init_entity(state)

        # Get current state
        new_value = self._get_state_value(state)

        if isinstance(new_value, numbers.Number):
            update_value = self._last_value is None or abs(new_value - self._last_value) >= self._value_delta

            if update_value or update_time:
                self._last_value = new_value
                new_state = round(new_value, self._precision)
                if update_time or new_state != self._state:
                    self._state = new_state
                    self._last_update = now_ts
                    self._updated = True
                    _LOGGER.info("Update [%s] time_trig[%d] upd_time[%d] upt_val[%d] val[%s] st[%s]",
                        self._name, time_trigger, update_time, update_value, new_value, new_state)
        else:
            self._last_value = None
            self._state = None
            self._last_update = now_ts
            self._updated = True
