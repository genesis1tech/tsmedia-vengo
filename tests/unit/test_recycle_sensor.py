"""
Unit tests for RecycleSensor (IR item detection for recycling verification).
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
    """Sensor config in simulation mode (no GPIO)"""
    return RecycleSensorConfig(
        gpio_pin=17,
        poll_interval=0.01,  # Fast polling for test speed
        simulation_mode=True,
        debounce_count=2,
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
        assert cfg.gpio_pin == 17
        assert cfg.poll_interval == 0.05
        assert cfg.active_low is True
        assert cfg.simulation_mode is False
        assert cfg.debounce_count == 2

    def test_env_override(self):
        env = {
            'TSV6_RECYCLE_SENSOR_GPIO': '27',
            'TSV6_RECYCLE_SENSOR_POLL_INTERVAL': '0.1',
            'TSV6_RECYCLE_SENSOR_SIMULATION': 'true',
            'TSV6_RECYCLE_SENSOR_DEBOUNCE': '5',
        }
        with patch.dict('os.environ', env):
            s = RecycleSensor()
            assert s.config.gpio_pin == 27
            assert s.config.poll_interval == 0.1
            assert s.config.simulation_mode is True
            assert s.config.debounce_count == 5
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
        sensor.stop_monitoring()

    def test_reset(self, sensor):
        sensor.start_monitoring()
        time.sleep(0.02)
        sensor.stop_monitoring()
        sensor.reset()
        assert sensor.state == SensorState.IDLE
        assert sensor.was_item_detected() is False
        assert sensor.get_detection_time() is None
        assert not sensor.detection_event.is_set()


class TestDetection:
    def test_detection_sets_event(self, sim_config):
        """When GPIO reads object, detection_event should be set"""
        sim_config.simulation_mode = False

        with patch('tsv6.hardware.recycle_sensor.RecycleSensor._setup_gpio', return_value=True):
            sensor = RecycleSensor(config=sim_config)

        # Mock _read_gpio to return True (object detected)
        with patch.object(sensor, '_read_gpio', return_value=True):
            sensor.start_monitoring()
            # Wait for detection (debounce_count=2 at 10ms interval ≈ 20ms)
            detected = sensor.detection_event.wait(timeout=1.0)
            sensor.stop_monitoring()

        assert detected is True
        assert sensor.was_item_detected() is True
        assert sensor.get_detection_time() is not None
        assert sensor.state == SensorState.DETECTED
        sensor.cleanup()

    def test_no_detection_event_timeout(self, sim_config):
        """detection_event.wait should timeout when no item detected"""
        sensor = RecycleSensor(config=sim_config)
        sensor.start_monitoring()

        detected = sensor.detection_event.wait(timeout=0.1)
        sensor.stop_monitoring()

        assert detected is False
        assert sensor.was_item_detected() is False
        sensor.cleanup()

    def test_detection_callback_called(self, sim_config):
        """on_detection callback should fire when item is detected"""
        sim_config.simulation_mode = False
        callback = MagicMock()

        with patch('tsv6.hardware.recycle_sensor.RecycleSensor._setup_gpio', return_value=True):
            sensor = RecycleSensor(config=sim_config, on_detection=callback)

        with patch.object(sensor, '_read_gpio', return_value=True):
            sensor.start_monitoring()
            sensor.detection_event.wait(timeout=1.0)
            sensor.stop_monitoring()

        callback.assert_called_once()
        sensor.cleanup()


class TestDebounce:
    def test_debounce_requires_consecutive_readings(self, sim_config):
        """Detection should require debounce_count consecutive positive readings"""
        sim_config.simulation_mode = False
        sim_config.debounce_count = 3

        with patch('tsv6.hardware.recycle_sensor.RecycleSensor._setup_gpio', return_value=True):
            sensor = RecycleSensor(config=sim_config)

        # Alternate True/False — should NOT trigger detection (breaks consecutive count)
        call_count = [0]
        def alternating_gpio():
            call_count[0] += 1
            return call_count[0] % 2 == 1  # True, False, True, False...

        with patch.object(sensor, '_read_gpio', side_effect=alternating_gpio):
            sensor.start_monitoring()
            detected = sensor.detection_event.wait(timeout=0.2)
            sensor.stop_monitoring()

        assert detected is False
        assert sensor.was_item_detected() is False
        sensor.cleanup()

    def test_debounce_passes_with_consecutive(self, sim_config):
        """Detection should pass with enough consecutive positive readings"""
        sim_config.simulation_mode = False
        sim_config.debounce_count = 3

        with patch('tsv6.hardware.recycle_sensor.RecycleSensor._setup_gpio', return_value=True):
            sensor = RecycleSensor(config=sim_config)

        # Return True consistently
        with patch.object(sensor, '_read_gpio', return_value=True):
            sensor.start_monitoring()
            detected = sensor.detection_event.wait(timeout=1.0)
            sensor.stop_monitoring()

        assert detected is True
        assert sensor.was_item_detected() is True
        sensor.cleanup()


class TestGPIOReading:
    def test_active_low_hi_means_no_object(self, sim_config):
        """In active-low mode, HI output means no object"""
        sim_config.simulation_mode = False
        sim_config.active_low = True

        with patch('tsv6.hardware.recycle_sensor.RecycleSensor._setup_gpio', return_value=True):
            sensor = RecycleSensor(config=sim_config)

        mock_result = MagicMock()
        mock_result.stdout = "17: ip pu | hi // GPIO17 = input"

        with patch('subprocess.run', return_value=mock_result):
            assert sensor._read_gpio() is False  # HI + active_low = not detected

        sensor.cleanup()

    def test_active_low_lo_means_object(self, sim_config):
        """In active-low mode, LO output means object detected"""
        sim_config.simulation_mode = False
        sim_config.active_low = True

        with patch('tsv6.hardware.recycle_sensor.RecycleSensor._setup_gpio', return_value=True):
            sensor = RecycleSensor(config=sim_config)

        mock_result = MagicMock()
        mock_result.stdout = "17: ip pu | lo // GPIO17 = input"

        with patch('subprocess.run', return_value=mock_result):
            assert sensor._read_gpio() is True  # LO + active_low = detected

        sensor.cleanup()

    def test_active_high_hi_means_object(self, sim_config):
        """In active-high mode, HI output means object detected"""
        sim_config.simulation_mode = False
        sim_config.active_low = False

        with patch('tsv6.hardware.recycle_sensor.RecycleSensor._setup_gpio', return_value=True):
            sensor = RecycleSensor(config=sim_config)

        mock_result = MagicMock()
        mock_result.stdout = "17: ip pu | hi // GPIO17 = input"

        with patch('subprocess.run', return_value=mock_result):
            assert sensor._read_gpio() is True  # HI + active_high = detected

        sensor.cleanup()

    def test_simulation_mode_returns_false(self, sensor):
        """Simulation mode should always return False (no detection)"""
        assert sensor._read_gpio() is False

    def test_gpio_error_returns_false(self, sim_config):
        """GPIO read error should return False (safe default)"""
        sim_config.simulation_mode = False

        with patch('tsv6.hardware.recycle_sensor.RecycleSensor._setup_gpio', return_value=True):
            sensor = RecycleSensor(config=sim_config)

        with patch('subprocess.run', side_effect=Exception("GPIO error")):
            assert sensor._read_gpio() is False

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

        with patch('tsv6.hardware.recycle_sensor.RecycleSensor._setup_gpio', return_value=True):
            sensor = RecycleSensor(config=sim_config)

        results = []

        def waiter():
            detected = sensor.detection_event.wait(timeout=2.0)
            results.append(detected)

        with patch.object(sensor, '_read_gpio', return_value=True):
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
        assert "gpio=17" in r
        assert "state=idle" in r
        assert "detected=False" in r
