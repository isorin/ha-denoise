"""Regression tests for the Denoise sensor."""

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from homeassistant.core import State

from custom_components.denoise import sensor as denoise


class DenoiseSensorTests(unittest.TestCase):
    source_id = "sensor.test_source"
    start = datetime(2026, 1, 1, tzinfo=UTC)

    def setUp(self):
        self.states = Mock()
        self.hass = SimpleNamespace(
            states=self.states,
            bus=Mock(),
            config=SimpleNamespace(units=SimpleNamespace(temperature_unit="°C")),
        )
        self.set_source(0, 0)

    def make_sensor(self, *, delta=0, average=None, update=None):
        return denoise.DenoiseSensor(
            self.hass,
            name="Test",
            unique_id=None,
            entity_id=self.source_id,
            value_delta=delta,
            precision=1,
            average_interval=None if average is None else timedelta(seconds=average),
            update_interval=None if update is None else timedelta(seconds=update),
        )

    def set_source(self, value, elapsed):
        timestamp = self.start + timedelta(seconds=elapsed)
        self.states.get.return_value = State(
            self.source_id,
            str(value),
            {"device_class": "power", "unit_of_measurement": "W"},
            last_changed=timestamp,
            last_updated=timestamp,
        )

    def at_time(self, elapsed):
        return patch.object(
            denoise.dt_util,
            "utcnow",
            return_value=self.start + timedelta(seconds=elapsed),
        )

    def reading(self, sensor, value, elapsed):
        self.set_source(value, elapsed)
        with self.at_time(elapsed):
            sensor._update_state()

    def scan(self, sensor, elapsed):
        with self.at_time(elapsed):
            sensor.update()

    def test_setup_enables_warning_logs_per_sensor(self):
        base = {"platform": "denoise", "entity_id": self.source_id}
        self.assertIs(denoise.PLATFORM_SCHEMA(base)["log_warn"], False)
        config = denoise.PLATFORM_SCHEMA(
            {**base, "log_warn": True, "value_delta": -1}
        )
        add_entities = Mock()
        with self.assertLogs(denoise.__name__, level=logging.WARNING) as logs:
            asyncio.run(denoise.async_setup_platform(self.hass, config, add_entities))
            sensor = add_entities.call_args.args[0][0]
            self.reading(sensor, 1, 0)

        self.assertEqual(sensor.state, 1)
        messages = [record.getMessage() for record in logs.records]
        self.assertTrue(messages[0].startswith("Setup [Denoise sensor]"))
        self.assertIn("Update [Denoise sensor] time_trig 0 upd_time 0", messages)

        other_sensor = self.make_sensor()
        with self.assertNoLogs(denoise.__name__, level=logging.WARNING):
            self.reading(other_sensor, 2, 1)

    def test_source_listener_publishes_at_startup_and_on_change(self):
        sensor = self.make_sensor()
        asyncio.run(sensor.async_added_to_hass())
        event_type, startup = self.hass.bus.async_listen_once.call_args.args
        self.assertEqual(event_type, denoise.EVENT_HOMEASSISTANT_START)

        with (
            patch.object(denoise, "async_track_state_change_event") as track,
            patch.object(sensor, "async_schedule_update_ha_state") as schedule,
        ):
            with self.at_time(0):
                startup(None)
            hass, entity_ids, listener = track.call_args.args
            self.assertIs(hass, self.hass)
            self.assertEqual(entity_ids, [self.source_id])
            self.assertEqual(sensor.state, 0)
            schedule.assert_called_once_with(False)

            schedule.reset_mock()
            self.set_source(42, 1)
            with self.at_time(1):
                listener(None)
            self.assertEqual(sensor.state, 42)
            schedule.assert_called_once_with(False)

    def test_delta_without_intervals(self):
        sensor = self.make_sensor(delta=0.3)
        self.assertFalse(sensor.should_poll)
        self.reading(sensor, 20, 0)
        self.reading(sensor, 20.2, 1)
        self.assertEqual(sensor.state, 20)

        self.reading(sensor, 20.4, 2)
        self.assertEqual(sensor.state, 20.4)

    def test_averaging_without_update_interval(self):
        sensor = self.make_sensor(average=30)
        self.reading(sensor, 20, 0)
        self.reading(sensor, 22, 10)
        self.assertEqual(sensor.state, 21)

        self.reading(sensor, 25, 41)
        self.assertEqual(sensor.state, 25)

    def test_early_scan_skips_source_lookup(self):
        sensor = self.make_sensor(delta=-1, average=180, update=60)
        self.reading(sensor, 100, 0)
        self.states.get.reset_mock()

        self.scan(sensor, 40)
        self.states.get.assert_not_called()
        self.assertFalse(sensor.force_update)

    def test_due_scan_skips_recent_source_update(self):
        sensor = self.make_sensor(delta=-1, average=180, update=60)
        self.reading(sensor, 100, 0)
        self.reading(sensor, 200, 45)

        self.scan(sensor, 60)
        self.assertEqual(sensor.state, 100)
        self.assertEqual(len(sensor._avg_deque), 2)
        self.assertFalse(sensor.force_update)

    def test_due_scan_uses_existing_samples_without_appending(self):
        sensor = self.make_sensor(delta=-1, average=180, update=60)
        self.reading(sensor, 100, 0)
        self.reading(sensor, 200, 30)

        self.scan(sensor, 90)
        self.assertEqual(sensor.state, 150)
        self.assertEqual([value for _, value in sensor._avg_deque], [100, 200])

    def test_due_scan_uses_source_when_all_samples_expire(self):
        sensor = self.make_sensor(delta=-1, average=30, update=60)
        self.reading(sensor, 100, 0)
        self.reading(sensor, 200, 20)

        self.scan(sensor, 80)
        self.assertEqual(sensor.state, 200)
        self.assertFalse(sensor._avg_deque)

    def test_scan_fallback_without_averaging(self):
        sensor = self.make_sensor(delta=-1, update=60)
        self.reading(sensor, 100, 0)
        self.reading(sensor, 200, 20)
        self.assertEqual(sensor.state, 100)

        self.scan(sensor, 80)
        self.assertEqual(sensor.state, 200)

    def test_timed_update_averages_the_five_latest_readings(self):
        sensor = self.make_sensor(delta=-1, average=270, update=270)
        for value in range(5):
            self.reading(sensor, value, value * 60)
            self.assertEqual(sensor.state, 0)

        self.reading(sensor, 5, 300)
        self.assertEqual(sensor.state, 3)
        self.assertEqual([value for _, value in sensor._avg_deque], [1, 2, 3, 4, 5])

    def test_timed_publish_updates_the_delta_reference(self):
        sensor = self.make_sensor(delta=0.399, update=300)
        self.reading(sensor, 20, 0)
        self.reading(sensor, 19.7, 300)
        self.assertEqual(sensor.state, 19.7)

        self.reading(sensor, 19.9, 310)
        self.assertEqual(sensor.state, 19.7)
        self.reading(sensor, 20.1, 320)
        self.assertEqual(sensor.state, 20.1)

    def test_due_unchanged_rounded_value_is_not_published(self):
        sensor = self.make_sensor(delta=-1, update=60)
        self.reading(sensor, 100, 0)
        self.reading(sensor, 100.04, 60)

        self.assertEqual(sensor.state, 100)
        self.assertFalse(sensor.force_update)

    def test_debug_averaging_log_formats_average_and_samples(self):
        sensor = self.make_sensor(average=270)
        with self.assertLogs(denoise.__name__, level=logging.DEBUG) as logs:
            self.reading(sensor, 384, 0)
            self.reading(sensor, 385, 60)
            self.reading(sensor, 385, 120)

        messages = [record.getMessage() for record in logs.records]
        self.assertIn("[Test] avg_val 384.667 samples [384.0, 385.0, 385.0]", messages)
