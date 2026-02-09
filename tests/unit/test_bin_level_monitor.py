"""Unit tests for Bin Level Monitor."""

import pytest
from unittest.mock import Mock, MagicMock

from src.tsv6.utils.bin_level_monitor import (
    BinLevelMonitor,
    BinLevelMonitorConfig,
    FILL_LEVEL_THRESHOLDS,
)


class TestBinLevelMonitorConfig:
    def test_default_config(self):
        config = BinLevelMonitorConfig()
        assert config.check_interval_secs == 1800.0
        assert config.startup_delay_secs == 30.0
        assert config.full_distance_mm == 150
        assert config.empty_distance_mm == 800
        assert config.max_consecutive_failures == 3


class TestFillPercentageCalculation:
    """Test the fill percentage formula: (empty - measured) / (empty - full) * 100"""

    def test_full_bin(self):
        pct = BinLevelMonitor.calculate_fill_percentage(150, 800, 150)
        assert pct == 100.0

    def test_empty_bin(self):
        pct = BinLevelMonitor.calculate_fill_percentage(800, 800, 150)
        assert pct == 0.0

    def test_half_full(self):
        pct = BinLevelMonitor.calculate_fill_percentage(475, 800, 150)
        assert pct == 50.0

    def test_quarter_full(self):
        pct = BinLevelMonitor.calculate_fill_percentage(637, 800, 150)
        # (800 - 637) / (800 - 150) * 100 = 163/650 * 100 = 25.08%
        assert 24.5 <= pct <= 25.5

    def test_three_quarter_full(self):
        pct = BinLevelMonitor.calculate_fill_percentage(312, 800, 150)
        # (800 - 312) / (800 - 150) * 100 = 488/650 * 100 = 75.08%
        assert 74.5 <= pct <= 75.5

    def test_clamp_above_100(self):
        """Distance closer than full_distance should clamp to 100%."""
        pct = BinLevelMonitor.calculate_fill_percentage(50, 800, 150)
        assert pct == 100.0

    def test_clamp_below_0(self):
        """Distance farther than empty_distance should clamp to 0%."""
        pct = BinLevelMonitor.calculate_fill_percentage(1000, 800, 150)
        assert pct == 0.0

    def test_invalid_distances_returns_zero(self):
        """If empty <= full (misconfiguration), return 0."""
        pct = BinLevelMonitor.calculate_fill_percentage(400, 150, 800)
        assert pct == 0.0

    def test_equal_distances_returns_zero(self):
        pct = BinLevelMonitor.calculate_fill_percentage(400, 400, 400)
        assert pct == 0.0


class TestFillLevelClassification:
    """Test named fill level thresholds."""

    def test_empty(self):
        assert BinLevelMonitor.fill_percentage_to_level(0.0) == "empty"
        assert BinLevelMonitor.fill_percentage_to_level(5.0) == "empty"
        assert BinLevelMonitor.fill_percentage_to_level(12.0) == "empty"

    def test_quarter(self):
        assert BinLevelMonitor.fill_percentage_to_level(13.0) == "quarter"
        assert BinLevelMonitor.fill_percentage_to_level(25.0) == "quarter"
        assert BinLevelMonitor.fill_percentage_to_level(37.0) == "quarter"

    def test_half(self):
        assert BinLevelMonitor.fill_percentage_to_level(38.0) == "half"
        assert BinLevelMonitor.fill_percentage_to_level(50.0) == "half"
        assert BinLevelMonitor.fill_percentage_to_level(62.0) == "half"

    def test_three_quarter(self):
        assert BinLevelMonitor.fill_percentage_to_level(63.0) == "three_quarter"
        assert BinLevelMonitor.fill_percentage_to_level(75.0) == "three_quarter"
        assert BinLevelMonitor.fill_percentage_to_level(87.0) == "three_quarter"

    def test_full(self):
        assert BinLevelMonitor.fill_percentage_to_level(88.0) == "full"
        assert BinLevelMonitor.fill_percentage_to_level(95.0) == "full"
        assert BinLevelMonitor.fill_percentage_to_level(100.0) == "full"

    def test_thresholds_are_sorted_descending(self):
        """Verify thresholds are checked from highest to lowest."""
        thresholds = [t for t, _ in FILL_LEVEL_THRESHOLDS]
        assert thresholds == sorted(thresholds, reverse=True)


class TestBinLevelMonitor:
    @pytest.fixture
    def mock_sensor(self):
        sensor = Mock()
        sensor.read_distance_mm.return_value = 475  # Half full
        return sensor

    @pytest.fixture
    def monitor(self, mock_sensor):
        return BinLevelMonitor(tof_sensor=mock_sensor)

    def test_initialization(self, monitor):
        assert monitor._consecutive_failures == 0
        assert monitor._latest_fill_data is None

    def test_take_reading_success(self, monitor, mock_sensor):
        monitor._take_reading()
        data = monitor.get_latest_fill_data()
        assert data is not None
        assert data['distance_mm'] == 475
        assert data['fill_level'] == 'half'
        assert 49.0 <= data['fill_percentage'] <= 51.0
        assert data['empty_distance_mm'] == 800
        assert data['full_distance_mm'] == 150
        assert data['timestamp'] > 0

    def test_take_reading_fires_callback(self, mock_sensor):
        callback = Mock()
        monitor = BinLevelMonitor(tof_sensor=mock_sensor, on_level_update=callback)
        monitor._take_reading()
        callback.assert_called_once()
        fill_data = callback.call_args[0][0]
        assert fill_data['fill_level'] == 'half'

    def test_take_reading_failure_increments_counter(self, monitor, mock_sensor):
        mock_sensor.read_distance_mm.return_value = None
        monitor._take_reading()
        assert monitor._consecutive_failures == 1
        assert monitor.get_latest_fill_data() is None

    def test_take_reading_failure_reports_to_error_recovery(self, mock_sensor):
        error_recovery = Mock()
        config = BinLevelMonitorConfig(max_consecutive_failures=2)
        monitor = BinLevelMonitor(
            tof_sensor=mock_sensor, config=config, error_recovery_system=error_recovery
        )
        mock_sensor.read_distance_mm.return_value = None

        # First failure - under threshold
        monitor._take_reading()
        error_recovery.report_error.assert_not_called()

        # Second failure - at threshold
        monitor._take_reading()
        error_recovery.report_error.assert_called_once()

    def test_take_reading_success_resets_failures(self, monitor, mock_sensor):
        mock_sensor.read_distance_mm.return_value = None
        monitor._take_reading()
        assert monitor._consecutive_failures == 1

        mock_sensor.read_distance_mm.return_value = 475
        monitor._take_reading()
        assert monitor._consecutive_failures == 0

    def test_take_reading_reports_success_to_error_recovery(self, mock_sensor):
        error_recovery = Mock()
        monitor = BinLevelMonitor(
            tof_sensor=mock_sensor, error_recovery_system=error_recovery
        )
        monitor._take_reading()
        error_recovery.report_success.assert_called_once_with("tof_sensor")

    def test_get_latest_fill_data_returns_copy(self, monitor, mock_sensor):
        monitor._take_reading()
        data1 = monitor.get_latest_fill_data()
        data2 = monitor.get_latest_fill_data()
        assert data1 == data2
        assert data1 is not data2  # Should be distinct copies

    def test_get_monitor_status(self, monitor, mock_sensor):
        monitor._take_reading()
        status = monitor.get_monitor_status()
        assert status['running'] is False  # Not started
        assert status['consecutive_failures'] == 0
        assert status['latest_fill_data'] is not None
        assert status['check_interval_secs'] == 1800.0

    def test_start_stop(self, monitor):
        monitor.start()
        assert monitor._thread is not None
        assert monitor._thread.is_alive()
        monitor.stop()

    def test_full_distance_reading(self, mock_sensor):
        mock_sensor.read_distance_mm.return_value = 100  # Closer than 150mm
        monitor = BinLevelMonitor(tof_sensor=mock_sensor)
        monitor._take_reading()
        data = monitor.get_latest_fill_data()
        assert data['fill_level'] == 'full'
        assert data['fill_percentage'] == 100.0

    def test_empty_distance_reading(self, mock_sensor):
        mock_sensor.read_distance_mm.return_value = 800
        monitor = BinLevelMonitor(tof_sensor=mock_sensor)
        monitor._take_reading()
        data = monitor.get_latest_fill_data()
        assert data['fill_level'] == 'empty'
        assert data['fill_percentage'] == 0.0
