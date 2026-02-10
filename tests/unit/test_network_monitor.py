"""
Unit tests for Network Monitor (observe-only).

The NetworkMonitor only observes WiFi state and emits callbacks.
Recovery is handled by NetworkManager (Layer 0) and the shell watchdog (Layer 2).
"""
import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from src.tsv6.utils.network_monitor import (
    NetworkMonitor,
    NetworkMonitorConfig,
    _run
)


class TestNetworkMonitorConfig:
    """Test NetworkMonitorConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = NetworkMonitorConfig()
        assert config.interface == "wlan0"
        assert config.check_interval_secs == 10.0
        assert config.weak_signal_threshold_dbm == -80
        assert config.ping_target_local == "8.8.8.8"
        assert config.ping_target_public == "8.8.8.8"
        assert config.max_backoff_secs == 300.0
        assert config.soft_recovery_threshold == 2
        assert config.intermediate_recovery_threshold == 4
        assert config.hard_recovery_threshold == 6
        assert config.critical_escalation_threshold == 12

    def test_custom_config(self):
        """Test custom configuration values."""
        config = NetworkMonitorConfig(
            interface="wlan1",
            check_interval_secs=5.0,
            weak_signal_threshold_dbm=-70,
        )
        assert config.interface == "wlan1"
        assert config.check_interval_secs == 5.0
        assert config.weak_signal_threshold_dbm == -70


class TestNetworkMonitor:
    """Test NetworkMonitor class."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return NetworkMonitorConfig(
            interface="wlan0",
            check_interval_secs=1.0,
        )

    @pytest.fixture
    def monitor(self, config):
        """Create NetworkMonitor instance for testing."""
        return NetworkMonitor(config=config)

    def test_initialization(self, config):
        """Test NetworkMonitor initialization."""
        monitor = NetworkMonitor(config=config)
        assert monitor.cfg == config
        assert monitor.on_status is None
        assert monitor.on_disconnect is None
        assert monitor.on_reconnect is None
        assert monitor.error_recovery is None
        assert monitor._last_connected is None
        assert monitor._consecutive_failures == 0

    def test_initialization_with_callbacks(self, config):
        """Test initialization with callback functions."""
        on_status = Mock()
        on_disconnect = Mock()
        on_reconnect = Mock()
        error_recovery = Mock()

        monitor = NetworkMonitor(
            config=config,
            on_status=on_status,
            on_disconnect=on_disconnect,
            on_reconnect=on_reconnect,
            error_recovery_system=error_recovery
        )

        assert monitor.on_status == on_status
        assert monitor.on_disconnect == on_disconnect
        assert monitor.on_reconnect == on_reconnect
        assert monitor.error_recovery == error_recovery

    def test_systemd_recovery_manager_accepted(self, config):
        """Test that systemd_recovery_manager param is accepted for API compat."""
        mock_recovery = Mock()
        # Should not raise
        monitor = NetworkMonitor(
            config=config,
            systemd_recovery_manager=mock_recovery,
        )
        # Parameter is accepted but not stored/used
        assert not hasattr(monitor, 'systemd_recovery')

    def test_no_recovery_methods_exist(self, monitor):
        """Verify recovery methods were removed (observe-only architecture)."""
        assert not hasattr(monitor, '_soft_recovery')
        assert not hasattr(monitor, '_intermediate_recovery')
        assert not hasattr(monitor, '_hard_recovery')
        assert not hasattr(monitor, '_recover')
        assert not hasattr(monitor, '_determine_recovery_action')
        assert not hasattr(monitor, '_trigger_wifi_provisioning')

    def test_get_ssid_success(self, monitor):
        """Test successful SSID retrieval."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (0, "MyWiFiNetwork", "")

            ssid = monitor._get_ssid()
            assert ssid == "MyWiFiNetwork"

    def test_get_ssid_failure_falls_back(self, monitor):
        """Test SSID retrieval falls back to plain iwgetid."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            # First call (/usr/sbin/iwgetid) fails, second (iwgetid) succeeds
            mock_run.side_effect = [
                (1, "", "not found"),
                (0, "FallbackSSID", ""),
            ]

            ssid = monitor._get_ssid()
            assert ssid == "FallbackSSID"
            assert mock_run.call_count == 2

    def test_get_ssid_both_fail(self, monitor):
        """Test SSID retrieval when both attempts fail."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (1, "", "Device not found")

            ssid = monitor._get_ssid()
            assert ssid == ""

    def test_get_rssi_success(self, monitor):
        """Test successful RSSI retrieval."""
        iwconfig_output = """wlan0     IEEE 802.11  ESSID:"MyWiFiNetwork"
          Mode:Managed  Frequency:2.437 GHz  Access Point: 00:11:22:33:44:55
          Bit Rate=72.2 Mb/s   Tx-Power=31 dBm
          Retry short limit:7   RTS thr:off   Fragment thr:off
          Power Management:on
          Link Quality=70/70  Signal level=-40 dBm
          Rx invalid nwid:0  Rx invalid crypt:0  Rx invalid frag:0
          Tx excessive retries:0  Invalid misc:0   Missed beacon:0"""

        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (0, iwconfig_output, "")

            rssi = monitor._get_rssi()
            assert rssi == -40

    def test_get_rssi_no_signal_info(self, monitor):
        """Test RSSI retrieval when signal info not available."""
        iwconfig_output = "wlan0     IEEE 802.11  ESSID:\"MyWiFiNetwork\""

        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (0, iwconfig_output, "")

            rssi = monitor._get_rssi()
            assert rssi is None

    def test_get_rssi_command_failure(self, monitor):
        """Test RSSI retrieval when iwconfig command fails."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (1, "", "No such device")

            rssi = monitor._get_rssi()
            assert rssi is None

    def test_ping_success(self, monitor):
        """Test successful ping."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (0, "", "")

            result = monitor._ping("8.8.8.8")
            assert result is True

    def test_ping_failure(self, monitor):
        """Test ping failure."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (1, "", "Destination Host Unreachable")

            result = monitor._ping("192.168.1.1")
            assert result is False

    def test_get_gateway_success(self, monitor):
        """Test successful gateway detection."""
        ip_route_output = """default via 192.168.1.1 dev wlan0 proto dhcp src 192.168.1.100 metric 303
192.168.1.0/24 dev wlan0 proto dhcp scope link src 192.168.1.100 metric 303"""

        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (0, ip_route_output, "")

            gateway = monitor._get_gateway()
            assert gateway == "192.168.1.1"

    def test_get_gateway_failure(self, monitor):
        """Test gateway detection failure."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (1, "", "No default route")

            gateway = monitor._get_gateway()
            assert gateway == monitor.cfg.ping_target_local

    def test_emit_callback_success(self, monitor):
        """Test successful callback emission."""
        callback = Mock()
        payload = {"test": "data"}

        monitor._emit(callback, payload)
        callback.assert_called_once_with(payload)

    def test_emit_callback_with_exception(self, monitor):
        """Test callback emission with exception."""
        callback = Mock(side_effect=Exception("Callback error"))
        payload = {"test": "data"}

        # Should not raise exception
        monitor._emit(callback, payload)
        callback.assert_called_once_with(payload)

    def test_emit_callback_none(self, monitor):
        """Test callback emission with None callback."""
        monitor._emit(None, {"test": "data"})
        # Should not raise exception

    def test_get_recovery_status_zeroed(self, monitor):
        """Test recovery status returns zeroed fields (observe-only)."""
        status = monitor.get_recovery_status()

        assert status["consecutive_failures"] == 0
        assert status["current_stage"] == "none"
        assert status["soft_attempts"] == 0
        assert status["intermediate_attempts"] == 0
        assert status["hard_attempts"] == 0
        assert status["last_recovery_time"] == 0
        assert status["backoff_delay"] == 0
        assert "gateway" in status
        assert "wifi_intentionally_disabled" in status

    def test_get_recovery_status_with_failures(self, monitor):
        """Test that consecutive_failures is tracked even though recovery is disabled."""
        monitor._consecutive_failures = 5

        status = monitor.get_recovery_status()
        assert status["consecutive_failures"] == 5
        # Recovery fields stay zeroed (no recovery actions)
        assert status["soft_attempts"] == 0
        assert status["current_stage"] == "none"

    def test_wifi_intentionally_disabled(self, monitor):
        """Test WiFi intentionally disabled flag for LTE-first mode."""
        assert monitor.is_wifi_intentionally_disabled() is False

        monitor.set_wifi_intentionally_disabled(True)
        assert monitor.is_wifi_intentionally_disabled() is True

        # Should reset failure count
        monitor._consecutive_failures = 5
        monitor.set_wifi_intentionally_disabled(True)
        assert monitor._consecutive_failures == 0

        monitor.set_wifi_intentionally_disabled(False)
        assert monitor.is_wifi_intentionally_disabled() is False

    def test_start_monitoring(self, monitor):
        """Test starting network monitoring."""
        with patch('threading.Thread') as mock_thread:
            mock_thread_instance = Mock()
            mock_thread.return_value = mock_thread_instance

            monitor.start()

            mock_thread.assert_called_once()
            assert mock_thread_instance.start.called
            assert monitor._thread == mock_thread_instance

    def test_start_monitoring_already_running(self, monitor):
        """Test starting monitoring when already running."""
        mock_thread = Mock()
        mock_thread.is_alive.return_value = True
        monitor._thread = mock_thread

        with patch('threading.Thread') as mock_thread_class:
            monitor.start()

            # Should not create new thread
            mock_thread_class.assert_not_called()

    def test_stop_monitoring(self, monitor):
        """Test stopping network monitoring."""
        mock_thread = Mock()
        monitor._thread = mock_thread

        monitor.stop()

        assert monitor._stop.is_set()
        mock_thread.join.assert_called_once_with(timeout=2)


class TestRunFunction:
    """Test the _run helper function."""

    def test_run_success(self):
        """Test successful command execution."""
        with patch('subprocess.run') as mock_run:
            mock_process = Mock()
            mock_process.returncode = 0
            mock_process.stdout = "output"
            mock_process.stderr = "error"
            mock_run.return_value = mock_process

            rc, out, err = _run(["echo", "hello"])

            assert rc == 0
            assert out == "output"
            assert err == "error"

    def test_run_with_timeout(self):
        """Test command execution with custom timeout."""
        with patch('subprocess.run') as mock_run:
            mock_process = Mock()
            mock_process.returncode = 0
            mock_process.stdout = "output"
            mock_process.stderr = ""
            mock_run.return_value = mock_process

            rc, out, err = _run(["sleep", "1"], timeout=10.0)

            assert rc == 0

    def test_run_exception(self):
        """Test command execution with exception."""
        with patch('subprocess.run', side_effect=Exception("Command failed")):
            rc, out, err = _run(["bad", "command"])

            assert rc == 1
            assert out == ""
            assert err == "Command failed"
