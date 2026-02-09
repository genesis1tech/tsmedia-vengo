"""Unit tests for VL53L0X ToF Sensor Controller."""

import pytest
from unittest.mock import patch, MagicMock

from src.tsv6.hardware.tof_sensor import (
    ToFSensor,
    ToFSensorConfig,
    OUT_OF_RANGE_VALUES,
)


class TestToFSensorConfig:
    def test_default_config(self):
        config = ToFSensorConfig()
        assert config.i2c_address == 0x29
        assert config.timing_budget_us == 200_000
        assert config.sample_count == 7
        assert config.full_distance_mm == 150
        assert config.empty_distance_mm == 800
        assert config.simulation_mode is False

    def test_custom_config(self):
        config = ToFSensorConfig(empty_distance_mm=1000, full_distance_mm=100, sample_count=5)
        assert config.empty_distance_mm == 1000
        assert config.full_distance_mm == 100
        assert config.sample_count == 5


class TestToFSensorSimulation:
    @pytest.fixture
    def sim_sensor(self):
        config = ToFSensorConfig(simulation_mode=True, simulation_distance_mm=400)
        sensor = ToFSensor(config=config)
        sensor.connect()
        return sensor

    def test_connect(self, sim_sensor):
        assert sim_sensor._connected is True

    def test_read_distance(self, sim_sensor):
        distance = sim_sensor.read_distance_mm()
        assert distance == 400

    def test_read_distance_multiple(self, sim_sensor):
        """Simulation always returns the same configured value."""
        d1 = sim_sensor.read_distance_mm()
        d2 = sim_sensor.read_distance_mm()
        assert d1 == d2 == 400

    def test_get_status(self, sim_sensor):
        sim_sensor.read_distance_mm()
        status = sim_sensor.get_status()
        assert status['connected'] is True
        assert status['last_distance_mm'] == 400
        assert status['simulation_mode'] is True
        assert status['i2c_address'] == '0x29'
        assert status['empty_distance_mm'] == 800
        assert status['full_distance_mm'] == 150
        assert status['last_read_time'] > 0

    def test_cleanup(self, sim_sensor):
        sim_sensor.cleanup()
        assert sim_sensor._connected is False

    def test_context_manager(self):
        config = ToFSensorConfig(simulation_mode=True)
        with ToFSensor(config=config) as sensor:
            sensor.connect()
            assert sensor._connected is True
        assert sensor._connected is False

    def test_read_before_connect(self):
        config = ToFSensorConfig(simulation_mode=True)
        sensor = ToFSensor(config=config)
        # Simulation mode doesn't require connect, but let's test not-connected path
        sensor.config.simulation_mode = False
        result = sensor.read_distance_mm()
        assert result is None


class TestToFSensorEnvOverrides:
    @patch.dict('os.environ', {
        'TSV6_TOF_EMPTY_DISTANCE': '1200',
        'TSV6_TOF_FULL_DISTANCE': '100',
        'TSV6_TOF_SIMULATION': 'true',
        'TSV6_TOF_SAMPLE_COUNT': '11',
        'TSV6_TOF_I2C_ADDRESS': '0x30',
    })
    def test_env_overrides(self):
        sensor = ToFSensor()
        assert sensor.config.empty_distance_mm == 1200
        assert sensor.config.full_distance_mm == 100
        assert sensor.config.simulation_mode is True
        assert sensor.config.sample_count == 11
        assert sensor.config.i2c_address == 0x30

    @patch.dict('os.environ', {'TSV6_TOF_EMPTY_DISTANCE': 'not_a_number'})
    def test_invalid_env_value_ignored(self):
        config = ToFSensorConfig(simulation_mode=True, empty_distance_mm=800)
        sensor = ToFSensor(config=config)
        # Invalid value should be ignored, keeping the original
        assert sensor.config.empty_distance_mm == 800


class TestToFSensorHardwareMock:
    """Test hardware read paths with mocked I2C/VL53L0X."""

    @pytest.fixture
    def mock_sensor(self):
        """Create a sensor with mocked hardware internals."""
        config = ToFSensorConfig(simulation_mode=True, sample_count=5, sample_delay_ms=0)
        sensor = ToFSensor(config=config)
        # Switch off simulation to test the hardware read path with a mock
        sensor.config.simulation_mode = False
        sensor._connected = True
        sensor._sensor = MagicMock()
        return sensor

    def test_read_valid_samples(self, mock_sensor):
        mock_sensor._sensor.range = 450
        # property access returns same value each time
        type(mock_sensor._sensor).range = property(lambda self: 450)
        distance = mock_sensor.read_distance_mm()
        assert distance == 450

    def test_read_filters_out_of_range(self, mock_sensor):
        """Out-of-range values (8190, 8191) should be excluded."""
        readings = iter([8190, 400, 8191, 410, 405])
        type(mock_sensor._sensor).range = property(lambda self: next(readings))
        distance = mock_sensor.read_distance_mm()
        # Median of [400, 410, 405] = 405
        assert distance == 405

    def test_read_all_out_of_range(self, mock_sensor):
        type(mock_sensor._sensor).range = property(lambda self: 8190)
        distance = mock_sensor.read_distance_mm()
        assert distance is None

    def test_read_handles_exceptions(self, mock_sensor):
        type(mock_sensor._sensor).range = property(lambda self: (_ for _ in ()).throw(OSError("I2C error")))
        distance = mock_sensor.read_distance_mm()
        assert distance is None

    def test_out_of_range_values_constant(self):
        assert 8190 in OUT_OF_RANGE_VALUES
        assert 8191 in OUT_OF_RANGE_VALUES
