"""
Unit tests for RecycleSensor (VL53L1X ToF two-detection recycling verification).
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from tsv6.hardware.recycle_sensor import (
    RecycleSensor,
    RecycleSensorConfig,
    SensorState,
)


@pytest.fixture
def sim_config():
    """Sensor config in simulation mode (no I2C hardware)"""
    return RecycleSensorConfig(
        i2c_bus=2,
        poll_interval=0.01,  # Fast polling for test speed
        simulation_mode=True,
        required_detections=2,
        detection_threshold_mm=110,
    )


@pytest.fixture
def sensor(sim_config):
    """RecycleSensor in simulation mode"""
    s = RecycleSensor(config=sim_config)
    yield s
    s.cleanup()


class TestRecycleSensorConfig:
    def test_defaults(self):
        cfg = RecycleSensorConfig()
        assert cfg.i2c_bus == 2
        assert cfg.i2c_address == 0x29
        assert cfg.poll_interval == 0.05
        assert cfg.detection_threshold_mm == 110
        assert cfg.distance_mode == 1
        assert cfg.timing_budget_ms == 50
        assert cfg.simulation_mode is False
        assert cfg.required_detections == 2
        assert cfg.baseline_sample_count == 5

    def test_env_override(self):
        env = {
            'TSV6_RECYCLE_SENSOR_I2C_BUS': '5',
            'TSV6_RECYCLE_SENSOR_I2C_ADDRESS': '0x30',
            'TSV6_RECYCLE_SENSOR_THRESHOLD_MM': '60',
            'TSV6_RECYCLE_SENSOR_POLL_INTERVAL': '0.1',
            'TSV6_RECYCLE_SENSOR_SIMULATION': 'true',
            'TSV6_RECYCLE_SENSOR_REQUIRED_DETECTIONS': '3',
            'TSV6_RECYCLE_SENSOR_DISTANCE_MODE': '2',
            'TSV6_RECYCLE_SENSOR_TIMING_BUDGET': '100',
        }
        with patch.dict('os.environ', env):
            s = RecycleSensor()
            assert s.config.i2c_bus == 5
            assert s.config.i2c_address == 0x30
            assert s.config.detection_threshold_mm == 60
            assert s.config.poll_interval == 0.1
            assert s.config.simulation_mode is True
            assert s.config.required_detections == 3
            assert s.config.distance_mode == 2
            assert s.config.timing_budget_ms == 100
            s.cleanup()


class TestSensorInitialization:
    def test_starts_idle(self, sensor):
        assert sensor.state == SensorState.IDLE
        assert sensor.was_item_detected() is False
        assert sensor.is_monitoring() is False

    def test_simulation_mode_set(self, sensor):
        assert sensor.config.simulation_mode is True

    def test_detection_event_starts_unset(self, sensor):
        assert not sensor.detection_event.is_set()

    def test_forces_simulation_when_library_unavailable(self):
        """Should force simulation mode when VL53L1X library is not available"""
        with patch('tsv6.hardware.recycle_sensor.VL53L1X_AVAILABLE', False):
            s = RecycleSensor(config=RecycleSensorConfig(simulation_mode=False))
            assert s.config.simulation_mode is True
            s.cleanup()

    def test_detection_count_starts_at_zero(self, sensor):
        assert sensor.get_detection_count() == 0


class TestMonitoringLifecycle:
    def test_start_monitoring(self, sensor):
        assert sensor.start_monitoring() is True
        assert sensor.state == SensorState.MONITORING
        assert sensor.is_monitoring() is True
        sensor.stop_monitoring()

    def test_stop_monitoring_no_detection(self, sensor):
        sensor.start_monitoring()
        time.sleep(0.05)
        result = sensor.stop_monitoring()
        assert result is False
        assert sensor.state == SensorState.NOT_DETECTED
        assert sensor.was_item_detected() is False

    def test_double_start_resets(self, sensor):
        sensor.start_monitoring()
        time.sleep(0.02)
        # Starting again should reset
        sensor.start_monitoring()
        assert sensor.state == SensorState.MONITORING
        assert sensor.get_detection_count() == 0
        sensor.stop_monitoring()

    def test_reset(self, sensor):
        sensor.start_monitoring()
        time.sleep(0.02)
        sensor.stop_monitoring()
        sensor.reset()
        assert sensor.state == SensorState.IDLE
        assert sensor.was_item_detected() is False
        assert sensor.get_detection_time() is None
        assert sensor.get_detection_count() == 0
        assert not sensor.detection_event.is_set()


class TestTwoDetectionVerification:
    def test_single_detection_not_enough(self, sim_config):
        """One beam-break (door only) should NOT trigger detection event"""
        sim_config.simulation_mode = False
        sim_config.required_detections = 2

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)

        # Simulate: one beam-break (True, True, False = one event)
        readings = iter([True, True, False, False, False, False, False])

        with patch.object(sensor, '_read_distance', side_effect=readings):
            sensor.start_monitoring()
            detected = sensor.detection_event.wait(timeout=0.2)
            sensor.stop_monitoring()

        assert detected is False
        assert sensor.get_detection_count() == 1
        assert sensor.was_item_detected() is False
        sensor.cleanup()

    def test_two_detections_triggers_success(self, sim_config):
        """Two beam-breaks (door + item) should trigger detection event"""
        sim_config.simulation_mode = False
        sim_config.required_detections = 2

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)

        # Simulate: door passes (True→False), then item passes (True→False)
        readings = iter([
            True, False,   # Detection #1 (door)
            False, False,  # Gap
            True, False,   # Detection #2 (item)
        ])

        with patch.object(sensor, '_read_distance', side_effect=readings):
            sensor.start_monitoring()
            detected = sensor.detection_event.wait(timeout=1.0)
            sensor.stop_monitoring()

        assert detected is True
        assert sensor.get_detection_count() == 2
        assert sensor.was_item_detected() is True
        assert sensor.get_detection_time() is not None
        sensor.cleanup()

    def test_detection_callback_on_second_event(self, sim_config):
        """on_detection callback should fire on the second beam-break"""
        sim_config.simulation_mode = False
        sim_config.required_detections = 2
        callback = MagicMock()

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config, on_detection=callback)

        # Door + item
        readings = iter([True, False, True, False])

        with patch.object(sensor, '_read_distance', side_effect=readings):
            sensor.start_monitoring()
            sensor.detection_event.wait(timeout=1.0)
            sensor.stop_monitoring()

        callback.assert_called_once()
        sensor.cleanup()

    def test_none_does_not_affect_beam_state(self, sim_config):
        """None (data not ready) should not change in_beam state"""
        sim_config.simulation_mode = False
        sim_config.required_detections = 2

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)

        # True, None, None, False (one event despite None gaps)
        # Then True, None, False (second event)
        readings = iter([
            True, None, None, False,  # Detection #1
            True, None, False,        # Detection #2
        ])

        with patch.object(sensor, '_read_distance', side_effect=readings):
            sensor.start_monitoring()
            detected = sensor.detection_event.wait(timeout=1.0)
            sensor.stop_monitoring()

        assert detected is True
        assert sensor.get_detection_count() == 2
        sensor.cleanup()

    def test_sustained_block_counts_as_one(self, sim_config):
        """Multiple consecutive below-threshold readings = one detection event"""
        sim_config.simulation_mode = False
        sim_config.required_detections = 2

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)

        # Many True readings in a row = still just one event
        readings = iter([
            True, True, True, True, False,  # Detection #1 (sustained)
            False, False,
            True, False,                     # Detection #2
        ])

        with patch.object(sensor, '_read_distance', side_effect=readings):
            sensor.start_monitoring()
            detected = sensor.detection_event.wait(timeout=1.0)
            sensor.stop_monitoring()

        assert detected is True
        assert sensor.get_detection_count() == 2
        sensor.cleanup()

    def test_no_detection_event_timeout(self, sim_config):
        """detection_event.wait should timeout when no item detected"""
        sensor = RecycleSensor(config=sim_config)
        sensor.start_monitoring()

        detected = sensor.detection_event.wait(timeout=0.1)
        sensor.stop_monitoring()

        assert detected is False
        assert sensor.was_item_detected() is False
        assert sensor.get_detection_count() == 0
        sensor.cleanup()

    def test_three_detections_required(self, sim_config):
        """Should support configurable required_detections"""
        sim_config.simulation_mode = False
        sim_config.required_detections = 3

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)

        # Two events — not enough for 3
        readings = iter([True, False, True, False, False, False, False])

        with patch.object(sensor, '_read_distance', side_effect=readings):
            sensor.start_monitoring()
            detected = sensor.detection_event.wait(timeout=0.2)
            sensor.stop_monitoring()

        assert detected is False
        assert sensor.get_detection_count() == 2
        sensor.cleanup()


class TestDistanceReading:
    def test_below_threshold_means_object(self, sim_config):
        """Distance below threshold means object detected"""
        sim_config.simulation_mode = False
        sim_config.detection_threshold_mm = 110

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)
            sensor._connected = True

        mock_sensor = MagicMock()
        mock_sensor.data_ready = True
        mock_sensor.distance = 5.0  # 50mm — below 110mm threshold
        sensor._sensor = mock_sensor

        assert sensor._read_distance() is True
        mock_sensor.clear_interrupt.assert_called_once()
        sensor.cleanup()

    def test_above_threshold_means_no_object(self, sim_config):
        """Distance above threshold means no object"""
        sim_config.simulation_mode = False
        sim_config.detection_threshold_mm = 110

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)
            sensor._connected = True

        mock_sensor = MagicMock()
        mock_sensor.data_ready = True
        mock_sensor.distance = 13.0  # 130mm — above 110mm threshold
        sensor._sensor = mock_sensor

        assert sensor._read_distance() is False
        sensor.cleanup()

    def test_data_not_ready_returns_none(self, sim_config):
        """When no new data is ready, return None"""
        sim_config.simulation_mode = False

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)
            sensor._connected = True

        mock_sensor = MagicMock()
        mock_sensor.data_ready = False
        sensor._sensor = mock_sensor

        assert sensor._read_distance() is None
        sensor.cleanup()

    def test_null_distance_returns_none(self, sim_config):
        """When sensor returns None distance, return None"""
        sim_config.simulation_mode = False

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)
            sensor._connected = True

        mock_sensor = MagicMock()
        mock_sensor.data_ready = True
        mock_sensor.distance = None
        sensor._sensor = mock_sensor

        assert sensor._read_distance() is None
        sensor.cleanup()

    def test_zero_distance_returns_none(self, sim_config):
        """When sensor returns 0 distance, return None (invalid reading)"""
        sim_config.simulation_mode = False

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)
            sensor._connected = True

        mock_sensor = MagicMock()
        mock_sensor.data_ready = True
        mock_sensor.distance = 0
        sensor._sensor = mock_sensor

        assert sensor._read_distance() is None
        sensor.cleanup()

    def test_simulation_mode_returns_false(self, sensor):
        """Simulation mode should always return False (no detection)"""
        assert sensor._read_distance() is False

    def test_read_error_returns_false(self, sim_config):
        """I2C read error should return False (safe default)"""
        sim_config.simulation_mode = False

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)
            sensor._connected = True

        mock_sensor = MagicMock()
        mock_sensor.data_ready = True
        type(mock_sensor).distance = property(
            lambda self: (_ for _ in ()).throw(OSError("I2C error"))
        )
        sensor._sensor = mock_sensor

        assert sensor._read_distance() is False
        sensor.cleanup()

    def test_not_connected_returns_false(self, sim_config):
        """When sensor not connected, return False"""
        sim_config.simulation_mode = False

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)
            sensor._connected = False

        assert sensor._read_distance() is False
        sensor.cleanup()


class TestThreadSafety:
    def test_concurrent_start_stop(self, sim_config):
        """Rapidly starting and stopping should not crash"""
        sensor = RecycleSensor(config=sim_config)

        for _ in range(10):
            sensor.start_monitoring()
            time.sleep(0.01)
            sensor.stop_monitoring()

        assert sensor.state in (SensorState.NOT_DETECTED, SensorState.IDLE)
        sensor.cleanup()

    def test_detection_event_usable_from_other_thread(self, sim_config):
        """detection_event should be usable from a different thread for wait"""
        sim_config.simulation_mode = False

        with patch(
            'tsv6.hardware.recycle_sensor.RecycleSensor._connect_sensor',
            return_value=True
        ):
            sensor = RecycleSensor(config=sim_config)

        results = []

        def waiter():
            detected = sensor.detection_event.wait(timeout=2.0)
            results.append(detected)

        # Two beam-break events
        readings = iter([True, False, True, False])

        with patch.object(sensor, '_read_distance', side_effect=readings):
            sensor.start_monitoring()
            wait_thread = threading.Thread(target=waiter)
            wait_thread.start()
            wait_thread.join(timeout=3.0)
            sensor.stop_monitoring()

        assert len(results) == 1
        assert results[0] is True
        sensor.cleanup()


class TestRepr:
    def test_repr(self, sensor):
        r = repr(sensor)
        assert "i2c_bus=2" in r
        assert "threshold=110mm" in r
        assert "state=idle" in r
        assert "detected=False" in r
