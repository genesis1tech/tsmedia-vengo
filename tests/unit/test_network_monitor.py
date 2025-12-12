"""
Unit tests for Network Monitor.
"""
import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from src.tsv6.utils.network_monitor import (
    NetworkMonitor,
    NetworkMonitorConfig,
    NetworkRecoveryStage,
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
        assert config.critical_escalation_threshold == 8


class TestNetworkRecoveryStage:
    """Test NetworkRecoveryStage class."""

    def test_initialization(self):
        """Test recovery stage initialization."""
        stage = NetworkRecoveryStage()
        assert stage.consecutive_failures == 0
        assert stage.soft_attempts == 0
        assert stage.intermediate_attempts == 0
        assert stage.hard_attempts == 0
        assert stage.last_recovery_time == 0
        assert stage.current_stage == "none"

    def test_reset(self):
        """Test recovery stage reset."""
        stage = NetworkRecoveryStage()
        stage.consecutive_failures = 5
        stage.soft_attempts = 2
        stage.intermediate_attempts = 1
        stage.hard_attempts = 1
        stage.current_stage = "hard"

        stage.reset()

        assert stage.consecutive_failures == 0
        assert stage.soft_attempts == 0
        assert stage.intermediate_attempts == 0
        assert stage.hard_attempts == 0
        assert stage.current_stage == "none"


class TestNetworkMonitor:
    """Test NetworkMonitor class."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return NetworkMonitorConfig(
            interface="wlan0",
            check_interval_secs=1.0,  # Fast for testing
            soft_recovery_threshold=2,
            intermediate_recovery_threshold=4,
            hard_recovery_threshold=6,
            critical_escalation_threshold=8
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
        assert monitor._backoff == 5.0
        assert isinstance(monitor._recovery, NetworkRecoveryStage)

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

    def test_get_ssid_success(self, monitor):
        """Test successful SSID retrieval."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run:
            mock_run.return_value = (0, "MyWiFiNetwork", "")

            ssid = monitor._get_ssid()
            assert ssid == "MyWiFiNetwork"
            mock_run.assert_called_once_with(["iwgetid", "-r"])

    def test_get_ssid_failure(self, monitor):
        """Test SSID retrieval failure."""
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
            mock_run.assert_called_once_with(["ping", "-c", "1", "-W", "1", "8.8.8.8"])

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

    def test_determine_recovery_action_none(self, monitor):
        """Test recovery action determination with no failures."""
        monitor._recovery.consecutive_failures = 0
        action = monitor._determine_recovery_action()
        assert action == "none"

    def test_determine_recovery_action_soft(self, monitor):
        """Test recovery action determination for soft recovery."""
        monitor._recovery.consecutive_failures = 3
        action = monitor._determine_recovery_action()
        assert action == "soft"

    def test_determine_recovery_action_intermediate(self, monitor):
        """Test recovery action determination for intermediate recovery."""
        monitor._recovery.consecutive_failures = 5
        action = monitor._determine_recovery_action()
        assert action == "intermediate"

    def test_determine_recovery_action_hard(self, monitor):
        """Test recovery action determination for hard recovery."""
        monitor._recovery.consecutive_failures = 7
        action = monitor._determine_recovery_action()
        assert action == "hard"

    def test_determine_recovery_action_escalate(self, monitor):
        """Test recovery action determination for escalation."""
        monitor._recovery.consecutive_failures = 10
        action = monitor._determine_recovery_action()
        assert action == "escalate"

    def test_soft_recovery_success(self, monitor):
        """Test successful soft recovery."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run, \
             patch('time.sleep') as mock_sleep:

            mock_run.return_value = (0, "", "")

            result = monitor._soft_recovery()
            assert result is True

            # Verify WPA reconfigure and DHCP commands were called
            assert mock_run.call_count >= 3

    def test_soft_recovery_failure(self, monitor):
        """Test soft recovery failure."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run, \
             patch('time.sleep') as mock_sleep:

            mock_run.side_effect = Exception("Command failed")

            result = monitor._soft_recovery()
            assert result is False

    def test_intermediate_recovery_with_error_recovery_system(self, monitor):
        """Test intermediate recovery using error recovery system."""
        mock_error_recovery = Mock()
        mock_error_recovery.reload_wifi_driver.return_value = True
        monitor.error_recovery = mock_error_recovery

        result = monitor._intermediate_recovery()
        assert result is True
        mock_error_recovery.reload_wifi_driver.assert_called_once()

    def test_intermediate_recovery_manual_fallback(self, monitor):
        """Test intermediate recovery manual fallback."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run, \
             patch('time.sleep') as mock_sleep, \
             patch('subprocess.run') as mock_subprocess_run:

            # Mock lsmod output
            mock_process = Mock()
            mock_process.stdout = "brcmfmac 123456\nbrcmutil 789012\n"
            mock_subprocess_run.return_value = mock_process

            mock_run.return_value = (0, "", "")

            result = monitor._intermediate_recovery()
            assert result is True

    def test_hard_recovery_success(self, monitor):
        """Test successful hard recovery."""
        with patch('src.tsv6.utils.network_monitor._run') as mock_run, \
             patch('time.sleep') as mock_sleep:

            mock_run.return_value = (0, "", "")

            result = monitor._hard_recovery()
            assert result is True

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

    def test_get_recovery_status(self, monitor):
        """Test recovery status retrieval."""
        monitor._recovery.consecutive_failures = 3
        monitor._recovery.current_stage = "soft"
        monitor._recovery.soft_attempts = 1
        monitor._recovery.intermediate_attempts = 0
        monitor._recovery.hard_attempts = 0
        monitor._recovery.last_recovery_time = 1234567890
        monitor._backoff = 10.0

        status = monitor.get_recovery_status()

        expected = {
            "consecutive_failures": 3,
            "current_stage": "soft",
            "soft_attempts": 1,
            "intermediate_attempts": 0,
            "hard_attempts": 0,
            "last_recovery_time": 1234567890,
            "backoff_delay": 10.0
        }

        assert status == expected

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
            mock_run.assert_called_once_with(["echo", "hello"], capture_output=True, text=True, timeout=5.0)

    def test_run_with_timeout(self):
        """Test command execution with custom timeout."""
        with patch('subprocess.run') as mock_run:
            mock_process = Mock()
            mock_process.returncode = 0
            mock_process.stdout = "output"
            mock_process.stderr = ""
            mock_run.return_value = mock_process

            rc, out, err = _run(["sleep", "1"], timeout=10.0)

            mock_run.assert_called_once_with(["sleep", "1"], capture_output=True, text=True, timeout=10.0)

    def test_run_exception(self):
        """Test command execution with exception."""
        with patch('subprocess.run', side_effect=Exception("Command failed")):
            rc, out, err = _run(["bad", "command"])

            assert rc == 1
            assert out == ""
            assert err == "Command failed"