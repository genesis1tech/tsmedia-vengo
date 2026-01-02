"""
Unit tests for LTE compact payload feature in ResilientAWSManager.

Tests verify:
1. LTE detection logic (_is_lte_primary)
2. Compact payload builder (_build_lte_status_payload)
3. Topic routing based on connection type
"""
import pytest
import json
import time
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import tempfile


class TestLTECompactPayload:
    """Test cases for LTE compact payload feature."""

    @pytest.fixture
    def temp_certs(self, tmp_path):
        """Create temporary certificate files for testing."""
        cert_dir = tmp_path / "certs"
        cert_dir.mkdir()

        cert_file = cert_dir / "cert.pem"
        key_file = cert_dir / "key.pem"
        ca_file = cert_dir / "ca.pem"

        cert_file.write_text("DUMMY CERT")
        key_file.write_text("DUMMY KEY")
        ca_file.write_text("DUMMY CA")

        return {
            'cert': str(cert_file),
            'key': str(key_file),
            'ca': str(ca_file)
        }

    @pytest.fixture
    def resilient_manager(self, temp_certs, tmp_path):
        """Create ResilientAWSManager instance for testing."""
        import uuid
        with patch.dict('sys.modules', {
            'board': Mock(),
            'busio': Mock(),
            'adafruit_pca9685': Mock(),
            'adafruit_motor': Mock(),
            'adafruit_motor.servo': Mock(),
            'awsiot': Mock(),
            'awscrt': Mock(),
            'awscrt.mqtt': Mock(),
            'awsiotsdk': Mock(),
        }):
            from tsv6.core.aws_resilient_manager import ResilientAWSManager, RetryConfig

            # Use unique lock file per test in tmp_path
            lock_file = str(tmp_path / f"tsv6-test-{uuid.uuid4()}.lock")
            manager = ResilientAWSManager(
                thing_name="TS-TEST-LTE",
                endpoint="test-endpoint.amazonaws.com",
                cert_path=temp_certs['cert'],
                key_path=temp_certs['key'],
                ca_path=temp_certs['ca'],
                retry_config=RetryConfig(initial_delay=0.1, max_delay=1.0),
                lock_file=lock_file
            )
            # Set connection start time for tests
            manager.connection_start_time = time.time() - 300
            yield manager
            # Cleanup
            manager._release_status_publish_lock()
            if Path(lock_file).exists():
                Path(lock_file).unlink()

    def test_lte_status_topic_initialized(self, resilient_manager):
        """Test that LTE status topic is correctly initialized."""
        assert resilient_manager.lte_status_topic == "device/TS-TEST-LTE/lte/status"

    def test_is_lte_primary_returns_true_when_wwan0_is_default(self, resilient_manager):
        """Test _is_lte_primary returns True when wwan0 is the default route."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "default via 10.64.64.64 dev wwan0 proto dhcp metric 700\n"

        with patch('subprocess.run', return_value=mock_result):
            assert resilient_manager._is_lte_primary() is True

    def test_is_lte_primary_returns_false_when_wlan0_is_default(self, resilient_manager):
        """Test _is_lte_primary returns False when wlan0 is the default route."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "default via 192.168.1.1 dev wlan0 proto dhcp metric 600\n"

        with patch('subprocess.run', return_value=mock_result):
            assert resilient_manager._is_lte_primary() is False

    def test_is_lte_primary_returns_false_on_error(self, resilient_manager):
        """Test _is_lte_primary returns False when subprocess fails."""
        with patch('subprocess.run', side_effect=Exception("Command failed")):
            assert resilient_manager._is_lte_primary() is False

    def test_is_lte_primary_uses_custom_interface(self, resilient_manager):
        """Test _is_lte_primary respects LTE_INTERFACE env var."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "default via 10.64.64.64 dev usb0 proto dhcp metric 700\n"

        with patch.dict('os.environ', {'LTE_INTERFACE': 'usb0'}):
            with patch('subprocess.run', return_value=mock_result):
                assert resilient_manager._is_lte_primary() is True

    def test_build_lte_status_payload_structure(self, resilient_manager):
        """Test compact LTE payload has correct structure with shortened keys."""
        resilient_manager.connection_start_time = time.time() - 600  # 10 minutes ago

        payload = resilient_manager._build_lte_status_payload(
            wifi_ssid="LTE Hologram",
            wifi_strength=75,
            cpu_temp=98.5
        )

        # Verify all required keys exist with correct short names
        assert "n" in payload  # thingName
        assert "s" in payload  # wifiSSID
        assert "w" in payload  # wifiStrength
        assert "t" in payload  # temperature
        assert "m" in payload  # timeConnectedMins
        assert "c" in payload  # connectionState

        # Verify values
        assert payload["n"] == "TS-TEST-LTE"
        assert payload["s"] == "LTE Hologram"
        assert payload["w"] == 75
        assert payload["t"] == 98.5
        assert payload["m"] == 10  # ~10 minutes
        assert payload["c"] == resilient_manager.state.value

    def test_build_lte_status_payload_size(self, resilient_manager):
        """Test that LTE payload is significantly smaller than full payload."""
        resilient_manager.connection_start_time = time.time() - 300

        lte_payload = resilient_manager._build_lte_status_payload(
            wifi_ssid="LTE Hologram",
            wifi_strength=75,
            cpu_temp=98.5
        )

        # Calculate size
        lte_size = len(json.dumps(lte_payload))

        # Full payload structure for comparison
        full_payload = {
            "state": {
                "reported": {
                    "thingName": "TS-TEST-LTE",
                    "deviceType": "raspberry-pi",
                    "firmwareVersion": "6.0.0",
                    "wifiSSID": "LTE Hologram",
                    "wifiStrength": 75,
                    "temperature": 98.5,
                    "timestampISO": "2026-01-02T12:00:00.000000Z",
                    "timeConnectedMins": 5,
                    "connectionState": "disconnected",
                    "messageId": "550e8400-e29b-41d4-a716-446655440000"
                }
            }
        }
        full_size = len(json.dumps(full_payload))

        # LTE payload should be at least 50% smaller
        reduction_pct = ((full_size - lte_size) / full_size) * 100
        assert reduction_pct >= 50, f"LTE payload should be at least 50% smaller, got {reduction_pct:.1f}%"

    def test_publish_status_uses_lte_topic_when_lte_primary(self, resilient_manager):
        """Test that publish_status uses LTE topic when LTE is primary connection."""
        # Reset the last publish time to allow publish
        from tsv6.core.aws_resilient_manager import ResilientAWSManager
        ResilientAWSManager._last_status_publish_time = 0

        # Simulate holding the lock
        resilient_manager._status_publish_lock_handle = Mock()

        mock_publish = Mock(return_value=True)

        with patch.object(resilient_manager, '_is_lte_primary', return_value=True), \
             patch.object(resilient_manager, '_get_wifi_info', return_value=("LTE Hologram", "75%")), \
             patch.object(resilient_manager, '_get_cpu_temperature', return_value=98.5), \
             patch.object(resilient_manager, 'publish_with_retry', mock_publish):

            result = resilient_manager.publish_status()

            assert result is True
            mock_publish.assert_called_once()

            # Verify LTE topic was used
            call_args = mock_publish.call_args
            assert call_args[0][0] == resilient_manager.lte_status_topic

            # Verify compact payload structure
            published_payload = call_args[0][1]
            assert "n" in published_payload
            assert "state" not in published_payload  # No shadow wrapper

    def test_publish_status_uses_shadow_topic_when_wifi_primary(self, resilient_manager):
        """Test that publish_status uses shadow topic when WiFi is primary connection."""
        # Reset the last publish time to allow publish
        from tsv6.core.aws_resilient_manager import ResilientAWSManager
        ResilientAWSManager._last_status_publish_time = 0

        # Simulate holding the lock
        resilient_manager._status_publish_lock_handle = Mock()

        mock_publish = Mock(return_value=True)

        with patch.object(resilient_manager, '_is_lte_primary', return_value=False), \
             patch.object(resilient_manager, '_get_wifi_info', return_value=("MyWiFi", -67)), \
             patch.object(resilient_manager, '_get_cpu_temperature', return_value=98.5), \
             patch.object(resilient_manager, 'publish_with_retry', mock_publish), \
             patch('tsv6.core.aws_resilient_manager.get_firmware_version', return_value="6.0.0"):

            result = resilient_manager.publish_status()

            assert result is True
            mock_publish.assert_called_once()

            # Verify shadow topic was used
            call_args = mock_publish.call_args
            assert call_args[0][0] == resilient_manager.shadow_update_topic

            # Verify full payload structure with shadow wrapper
            published_payload = call_args[0][1]
            assert "state" in published_payload
            assert "reported" in published_payload["state"]
            assert "thingName" in published_payload["state"]["reported"]

    def test_lte_signal_strength_parsing(self, resilient_manager):
        """Test that percentage signal strength is correctly parsed for LTE payload."""
        # Reset the last publish time to allow publish
        from tsv6.core.aws_resilient_manager import ResilientAWSManager
        ResilientAWSManager._last_status_publish_time = 0

        # Simulate holding the lock
        resilient_manager._status_publish_lock_handle = Mock()

        mock_publish = Mock(return_value=True)

        with patch.object(resilient_manager, '_is_lte_primary', return_value=True), \
             patch.object(resilient_manager, '_get_wifi_info', return_value=("LTE Hologram", "85%")), \
             patch.object(resilient_manager, '_get_cpu_temperature', return_value=98.5), \
             patch.object(resilient_manager, 'publish_with_retry', mock_publish):

            resilient_manager.publish_status()

            call_args = mock_publish.call_args
            published_payload = call_args[0][1]

            # Signal strength should be integer 85, not "85%"
            assert published_payload["w"] == 85
            assert isinstance(published_payload["w"], int)

    def test_lte_signal_strength_connecting(self, resilient_manager):
        """Test that 'Connecting...' signal strength is handled correctly."""
        # Reset the last publish time to allow publish
        from tsv6.core.aws_resilient_manager import ResilientAWSManager
        ResilientAWSManager._last_status_publish_time = 0

        # Simulate holding the lock
        resilient_manager._status_publish_lock_handle = Mock()

        mock_publish = Mock(return_value=True)

        with patch.object(resilient_manager, '_is_lte_primary', return_value=True), \
             patch.object(resilient_manager, '_get_wifi_info', return_value=("LTE Hologram", "Connecting...")), \
             patch.object(resilient_manager, '_get_cpu_temperature', return_value=98.5), \
             patch.object(resilient_manager, 'publish_with_retry', mock_publish):

            resilient_manager.publish_status()

            call_args = mock_publish.call_args
            published_payload = call_args[0][1]

            # Signal strength should be -1 for "Connecting..."
            assert published_payload["w"] == -1
