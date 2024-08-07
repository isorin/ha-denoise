"""
Denoise sensor
"""
import datetime
import logging
import math
import numbers
from typing import Union, Optional, Dict, Any
from collections import deque

import homeassistant.util.dt as dt_util
import voluptuous as vol
from homeassistant.components import history
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.group import expand_entity_ids
from homeassistant.components.recorder.models import LazyState
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass
)
from homeassistant.components.water_heater import DOMAIN as WATER_HEATER_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.const import (
    CONF_NAME,
    CONF_UNIQUE_ID,
    CONF_ENTITY_ID,
    EVENT_HOMEASSISTANT_START,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
    ATTR_ICON,
    ATTR_DEVICE_CLASS,
)

from homeassistant.core import (
    Event,
    EventStateChangedData,
    callback,
    split_entity_id
)

from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.unit_conversion import TemperatureConverter
from homeassistant.util.unit_system import TEMPERATURE_UNITS

DEFAULT_NAME = "Denoise sensor"

CONF_AVERAGE_INTERVAL = "average_interval"
CONF_VALUE_DELTA = "value_delta"
CONF_PRECISION = "precision"
CONF_UPDATE_INTERVAL = "update_interval"

class DOMAIN_TYPE:
    WEATHER = 1
    CLIMATE = 2
    OTHER = 3

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_AVERAGE_INTERVAL): cv.time_period,
        vol.Optional(CONF_VALUE_DELTA, default=0): vol.Any(int, float),
        vol.Optional(CONF_UPDATE_INTERVAL): cv.time_period,
        vol.Optional(CONF_PRECISION, default=1): int,
    }
)

# pylint: disable=unused-argument
async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up platform."""
    name = config.get(CONF_NAME)
    unique_id = config.get(CONF_UNIQUE_ID)
    average_interval = config.get(CONF_AVERAGE_INTERVAL)
    value_delta = config.get(CONF_VALUE_DELTA)
    update_interval = config.get(CONF_UPDATE_INTERVAL)
    entity_id = config.get(CONF_ENTITY_ID)
    precision = config.get(CONF_PRECISION)

    _LOGGER.info("Setup [%s] unique_id[%s] entity_id[%s] val_delta[%s] prec[%s] average_interval[%s] update_interval[%s]",
        name, unique_id, value_delta, precision, average_interval, update_interval)

    async_add_entities(
        [DenoiseSensor(hass, name, unique_id, entity_id, value_delta, precision, average_interval, update_interval)]
    )

# pylint: disable=r0902
class DenoiseSensor(SensorEntity):
    """Implementation of the Denoise sensor."""

    # pylint: disable=r0913
    def __init__(
        self,
        hass,
        name,
        unique_id,
        entity_id,
        value_delta,
        precision,
        average_interval,
        update_interval,
    ):
        """Initialize the sensor."""
        self._hass = hass
        self._name = name
        self._unique_id = unique_id
        self._src_entity_id = entity_id
        self._precision = precision
        self._value_delta = value_delta
        self._average_interval = average_interval
        self._update_interval = update_interval
        self._state = None
        self._unit_of_measurement = None
        self._device_class = None
        self._icon = None
        self._temperature_mode = None
        self._last_value = None
        self._last_update = None
        self._updated = False
        self._avg_deque = None if average_interval is None else deque(())

    @property
    def force_update(self) -> bool:
        """Return True if state updates should be forced.
        If True, a state change will be triggered anytime the state property is
        updated, not just when the value changes.
        """
        return self._updated

    @property
    def _has_update_interval(self) -> bool:
        """Return True if sensor has an update_interval setting."""
        return self._update_interval is not None

    @property
    def should_poll(self) -> bool:
        """Return the polling state."""
        return self._has_update_interval

    @property
    def name(self) -> Optional[str]:
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self) -> Optional[str]:
        """Return the unique ID of the sensor."""
        return self._unique_id

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
    def state_class(self) -> SensorStateClass | None:
        """Return the state class of this entity."""
        return SensorStateClass.MEASUREMENT

    @property
    def icon(self) -> Optional[str]:
        """Return the icon to use in the frontend."""
        return self._icon

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        # pylint: disable=unused-argument
        @callback
        def sensor_state_listener(event: Event[EventStateChangedData]):
            """Handle device state changes."""
            self._update_state()
            if self.force_update:
                self.async_schedule_update_ha_state(False)

        # pylint: disable=unused-argument
        @callback
        def sensor_startup(event):
            """Track changes and initial update"""
            async_track_state_change_event(
                self._hass,
                [self._src_entity_id],
                sensor_state_listener,
            )
            sensor_state_listener(None)

        self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, sensor_startup)

    def update(self):
        """Update the sensor state if it needed."""
        if self._has_update_interval:
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
        self._device_class = SensorDeviceClass.TEMPERATURE
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

        temperature = TemperatureConverter.convert(float(temperature), self._src_uom, self._unit_of_measurement)
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

    def _get_avg_value(self, now_ts, new_value):
        # remove any old values from the left
        while self._avg_deque and now_ts - self._avg_deque[0][0] > self._average_interval:
            self._avg_deque.popleft()
        # insert new value to the right
        self._avg_deque.append((now_ts, new_value))
        avg_val = sum(v[1] for v in self._avg_deque) / len(self._avg_deque)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("[%s] avg_val [%s] %s", self._name, avg_val, [v[1] for v in self._avg_deque])
        return avg_val

    def _update_state(self, time_trigger=False):  # pylint: disable=r0914,r0912,r0915
        """Update the sensor state."""

        self._updated = False
        now_ts = dt_util.utcnow()

        update_time = (self._has_update_interval and (self._last_update is None or
            now_ts - self._last_update >= self._update_interval))

        if time_trigger and (not update_time and self._average_interval is None):
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
            _LOGGER.debug("[%s] new_value [%s]", self._name, new_value)

            if self._average_interval is not None:
                new_value = self._get_avg_value(now_ts, new_value)

            update_value = self._last_value is None or abs(new_value - self._last_value) >= self._value_delta
            new_state = round(new_value, self._precision)
            state_changed = new_state != self._state

            if update_time or (update_value and state_changed):
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug("Update [%s] time_trig[%d] upd_time[%d] upt_val[%d] val[%s->%s] st[%s->%s]",
                        self._name, time_trigger, update_time, update_value, self._last_value, new_value, self._state, new_state)
                self._state = new_state
                self._last_update = now_ts
                self._updated = True

            if update_value:
                self._last_value = new_value

        else:
            self._last_value = None
            self._state = None
            self._last_update = now_ts
            self._updated = True
