"""
Unit tests for AWS IoT Manager.
"""
import pytest
import json
import time
from unittest.mock import Mock, patch, MagicMock, call
from pathlib import Path
import tempfile
import os


class TestAWSManager:
    """Test cases for AWSManager class."""

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
    def aws_manager(self, temp_certs):
        """Create AWSManager instance for testing."""
        # Mock board module before importing anything that uses it
        mock_board = Mock()
        mock_board.SCL = 3
        mock_board.SDA = 2

        with patch.dict('sys.modules', {
            'board': mock_board,
            'busio': Mock(),
            'adafruit_pca9685': Mock(),
            'adafruit_motor': Mock(),
            'adafruit_motor.servo': Mock(),
            'awsiot': Mock(),
            'awscrt': Mock(),
            'awsiotsdk': Mock(),
        }):
            # Force AWS_IOT_AVAILABLE to True for testing
            with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True):
                from tsv6.core.aws_manager import AWSManager

                manager = AWSManager(
                    thing_name="TS-TEST1234",
                    endpoint="test-endpoint.amazonaws.com",
                    cert_path=temp_certs['cert'],
                    key_path=temp_certs['key'],
                    ca_path=temp_certs['ca']
                )
                return manager

    def test_initialization(self, aws_manager):
        """Test AWSManager initialization."""
        assert aws_manager.thing_name == "TS-TEST1234"
        assert aws_manager.endpoint == "test-endpoint.amazonaws.com"
        assert aws_manager.client_id == "TS-TEST1234"
        assert aws_manager.connected is False
        assert aws_manager.connection is None
        assert aws_manager.connection_start_time is None

        # Check topics are set correctly
        assert aws_manager.status_topic == "device/TS-TEST1234/status"
        assert aws_manager.barcode_topic == "device/TS-TEST1234/barcode"
        assert aws_manager.command_topic == "device/TS-TEST1234/command"
        assert aws_manager.shadow_update_topic == "$aws/things/TS-TEST1234/shadow/update"

    def test_set_image_display_callback(self, aws_manager):
        """Test setting image display callback."""
        callback = Mock()
        aws_manager.set_image_display_callback(callback)
        assert aws_manager.image_display_callback == callback

    def test_set_no_match_display_callback(self, aws_manager):
        """Test setting no match display callback."""
        callback = Mock()
        aws_manager.set_no_match_display_callback(callback)
        assert aws_manager.no_match_display_callback == callback

    def test_connect_simulated_mode(self, aws_manager):
        """Test connection in simulated mode when AWS SDK not available."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', False):
            result = aws_manager.connect()
            assert result is True
            assert aws_manager.connected is True
            assert aws_manager.connection_start_time is not None

    def test_connect_success(self, aws_manager):
        """Test successful AWS IoT connection."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True), \
             patch('tsv6.core.aws_manager.mqtt_connection_builder') as mock_builder, \
             patch('tsv6.core.aws_manager.io') as mock_io:

            # Mock the connection
            mock_connection = Mock()
            mock_connect_future = Mock()
            mock_connect_future.result.return_value = None
            mock_connection.connect.return_value = mock_connect_future

            mock_builder.mtls_from_path.return_value = mock_connection

            # Mock event loop group
            mock_event_loop = Mock()
            mock_io.EventLoopGroup.return_value = mock_event_loop
            mock_io.DefaultHostResolver.return_value = Mock()
            mock_io.ClientBootstrap.return_value = Mock()

            with patch.object(aws_manager, '_subscribe_to_commands') as mock_subscribe:
                result = aws_manager.connect()

                assert result is True
                assert aws_manager.connected is True
                assert aws_manager.connection == mock_connection
                assert aws_manager.connection_start_time is not None

                # Verify connection builder was called with correct parameters
                mock_builder.mtls_from_path.assert_called_once()
                call_args = mock_builder.mtls_from_path.call_args
                assert call_args[1]['endpoint'] == "test-endpoint.amazonaws.com"
                assert call_args[1]['client_id'] == "TS-TEST1234"

                # Verify subscribe was called
                mock_subscribe.assert_called_once()

    def test_connect_missing_certificates(self, aws_manager, tmp_path):
        """Test connection failure when certificates are missing."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True):
            # Remove certificate files
            aws_manager.cert_path = str(tmp_path / "missing_cert.pem")

            result = aws_manager.connect()
            assert result is False
            assert aws_manager.connected is False

    def test_connect_failure(self, aws_manager):
        """Test connection failure due to exception."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True), \
             patch('tsv6.core.aws_manager.mqtt_connection_builder') as mock_builder:

            mock_builder.mtls_from_path.side_effect = Exception("Connection failed")

            result = aws_manager.connect()
            assert result is False
            assert aws_manager.connected is False

    def test_get_wifi_info_success(self, aws_manager):
        """Test successful WiFi info retrieval."""
        with patch('subprocess.run') as mock_run:
            # Mock iwgetid command
            mock_iwgetid = Mock()
            mock_iwgetid.returncode = 0
            mock_iwgetid.stdout = "MyWiFiNetwork\n"

            # Mock iwconfig command
            mock_iwconfig = Mock()
            mock_iwconfig.returncode = 0
            mock_iwconfig.stdout = "wlan0     IEEE 802.11  ESSID:\"MyWiFiNetwork\"\n          Signal level=-45 dBm\n"

            mock_run.side_effect = [mock_iwgetid, mock_iwconfig]

            ssid, rssi = aws_manager._get_wifi_info()
            assert ssid == "MyWiFiNetwork"
            assert rssi == -45

    def test_get_wifi_info_failure(self, aws_manager):
        """Test WiFi info retrieval failure."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("Command failed")

            ssid, rssi = aws_manager._get_wifi_info()
            assert ssid == "Unknown"
            assert rssi == -100

    def test_get_cpu_temperature_success(self, aws_manager):
        """Test successful CPU temperature reading."""
        with patch('builtins.open', create=True) as mock_open:
            mock_file = Mock()
            mock_file.read.return_value = "45000\n"  # 45.0°C
            mock_open.return_value.__enter__.return_value = mock_file

            temp = aws_manager._get_cpu_temperature()
            assert temp == 113.0  # (45 * 9/5) + 32

    def test_get_cpu_temperature_failure(self, aws_manager):
        """Test CPU temperature reading failure."""
        with patch('builtins.open', side_effect=Exception("File not found")):
            temp = aws_manager._get_cpu_temperature()
            assert temp == 75.0

    def test_get_bin_level(self, aws_manager):
        """Test bin level retrieval (currently returns default)."""
        level = aws_manager._get_bin_level()
        assert level == 25

    def test_publish_barcode_success(self, aws_manager):
        """Test successful barcode publishing."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True):
            aws_manager.connected = True
            aws_manager.connection = Mock()

            mock_publish_future = Mock()
            mock_publish_future.result.return_value = None
            aws_manager.connection.publish.return_value = (mock_publish_future, 123)

            with patch('tsv6.core.aws_manager.time') as mock_time, \
                 patch('tsv6.core.aws_manager.config') as mock_config:

                mock_time.time.return_value = 1640995200  # 2022-01-01 00:00:00 UTC
                mock_config.device.device_id = "TS-TEST1234"
                mock_config.device.DEVICE_LOCATION = "Test Location"

                result = aws_manager.publish_barcode("123456789012")

                assert result is True
                aws_manager.connection.publish.assert_called_once()

                # Verify the published message structure
                call_args = aws_manager.connection.publish.call_args
                published_payload = json.loads(call_args[1]['payload'])
                assert published_payload['barcode'] == "123456789012"
                assert published_payload['device_id'] == "TS-TEST1234"
                assert published_payload['thing_name'] == "TS-TEST1234"

    def test_publish_barcode_not_connected(self, aws_manager):
        """Test barcode publishing when not connected."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True):
            aws_manager.connected = False

            result = aws_manager.publish_barcode("123456789012")
            assert result is False

    def test_publish_status_success(self, aws_manager):
        """Test successful status publishing."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True):
            aws_manager.connected = True
            aws_manager.connection = Mock()
            aws_manager.connection_start_time = time.time() - 300  # 5 minutes ago

            mock_publish_future = Mock()
            mock_publish_future.result.return_value = None
            aws_manager.connection.publish.return_value = (mock_publish_future, 123)

            with patch('tsv6.core.aws_manager.config') as mock_config, \
                 patch.object(aws_manager, '_get_wifi_info', return_value=("TestWiFi", -45)), \
                 patch.object(aws_manager, '_get_cpu_temperature', return_value=75.0), \
                 patch.object(aws_manager, '_get_bin_level', return_value=50):

                # Mock config values
                mock_config.device.device_id = "TS-TEST1234"
                mock_config.device.FIRMWARE_VERSION = "1.0.0"
                mock_config.device.DEVICE_TYPE = "TSV6"
                mock_config.device.DEVICE_CLIENT = "RaspberryPi"
                mock_config.device.DEVICE_LOCATION = "Test Location"
                mock_config.device.WARRANTY_START_DATE = "2024-01-01"
                mock_config.device.WARRANTY_END_DATE = "2025-01-01"

                result = aws_manager.publish_status()

                assert result is True
                aws_manager.connection.publish.assert_called_once()

                # Verify shadow update topic
                call_args = aws_manager.connection.publish.call_args
                assert call_args[1]['topic'] == "$aws/things/TS-TEST1234/shadow/update"

                # Verify shadow message structure
                shadow_payload = json.loads(call_args[1]['payload'])
                assert 'state' in shadow_payload
                assert 'reported' in shadow_payload['state']
                reported = shadow_payload['state']['reported']
                assert reported['thingName'] == "TS-TEST1234"
                assert reported['wifiSSID'] == "TestWiFi"
                assert reported['temperature'] == 75.0

    def test_maintain_connection_already_connected(self, aws_manager):
        """Test connection maintenance when already connected."""
        aws_manager.connected = True

        result = aws_manager.maintain_connection()
        assert result is True

    def test_maintain_connection_reconnect(self, aws_manager):
        """Test connection maintenance triggering reconnect."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True):
            aws_manager.connected = False

            with patch.object(aws_manager, 'connect', return_value=True) as mock_connect:
                result = aws_manager.maintain_connection()
                assert result is True
                mock_connect.assert_called_once()

    def test_disconnect_success(self, aws_manager):
        """Test successful disconnection."""
        aws_manager.connected = True
        aws_manager.connection = Mock()
        aws_manager.servo_controller = Mock()

        mock_disconnect_future = Mock()
        mock_disconnect_future.result.return_value = None
        aws_manager.connection.disconnect.return_value = mock_disconnect_future

        connection_mock = aws_manager.connection

        aws_manager.disconnect()

        assert aws_manager.connected is False
        assert aws_manager.connection is None
        assert aws_manager.connection_start_time is None
        connection_mock.disconnect.assert_called_once()
        aws_manager.servo_controller.cleanup.assert_called_once()

    def test_disconnect_not_connected(self, aws_manager):
        """Test disconnection when not connected."""
        aws_manager.connected = False
        aws_manager.connection = None

        # Should not raise any exceptions
        aws_manager.disconnect()

    def test_register_message_handler_success(self, aws_manager):
        """Test successful message handler registration."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True):
            aws_manager.connected = True
            aws_manager.connection = Mock()

            mock_subscribe_future = Mock()
            mock_subscribe_future.result.return_value = None
            aws_manager.connection.subscribe.return_value = (mock_subscribe_future, 123)

            handler = Mock()
            result = aws_manager.register_message_handler("test/topic", handler)

            assert result is True
            aws_manager.connection.subscribe.assert_called_once_with(
                topic="test/topic",
                qos=1,  # mqtt.QoS.AT_LEAST_ONCE
                callback=handler
            )

    def test_register_message_handler_not_connected(self, aws_manager):
        """Test message handler registration when not connected."""
        with patch('tsv6.core.aws_manager.AWS_IOT_AVAILABLE', True):
            aws_manager.connected = False

            result = aws_manager.register_message_handler("test/topic", Mock())
            assert result is False

    def test_context_manager(self, aws_manager):
        """Test context manager functionality."""
        with patch.object(aws_manager, 'disconnect') as mock_disconnect:
            with aws_manager as manager:
                assert manager == aws_manager

            mock_disconnect.assert_called_once()

    def test_on_command_received(self, aws_manager):
        """Test command message handling."""
        payload = json.dumps({"action": "test", "data": "value"}).encode('utf-8')

        # Should not raise exceptions
        aws_manager._on_command_received("test/topic", payload, False, 1, False)

    def test_on_barcode_response_received_valid(self, aws_manager):
        """Test valid barcode response handling."""
        payload = json.dumps({
            "thingName": "TS-TEST1234",
            "returnAction": "openDoor",
            "productName": "Test Product",
            "productBrand": "Test Brand",
            "barcode": "123456789012"
        }).encode('utf-8')

        mock_image_callback = Mock()
        mock_servo = Mock()
        aws_manager.image_display_callback = mock_image_callback
        aws_manager.servo_controller = mock_servo

        aws_manager._on_barcode_response_received("test/topic", payload, False, 1, False)

        mock_image_callback.assert_called_once()
        mock_servo._set_angle.assert_any_call(90)
        mock_servo._set_angle.assert_any_call(0)

    def test_on_barcode_response_received_wrong_device(self, aws_manager):
        """Test barcode response for different device."""
        payload = json.dumps({
            "thingName": "DIFFERENT-DEVICE",
            "returnAction": "openDoor"
        }).encode('utf-8')

        mock_image_callback = Mock()
        mock_servo = Mock()
        aws_manager.image_display_callback = mock_image_callback
        aws_manager.servo_controller = mock_servo

        aws_manager._on_barcode_response_received("test/topic", payload, False, 1, False)

        # Callbacks should not be triggered for different device
        mock_image_callback.assert_not_called()
        mock_servo.open_door.assert_not_called()

    def test_on_no_match_received(self, aws_manager):
        """Test no match response handling."""
        payload = json.dumps({"returnAction": "noMatch"}).encode('utf-8')

        mock_no_match_callback = Mock()
        aws_manager.no_match_display_callback = mock_no_match_callback

        aws_manager._on_no_match_received("test/topic", payload, False, 1, False)

        mock_no_match_callback.assert_called_once()

    def test_set_ota_manager(self, aws_manager):
        """Test setting OTA manager."""
        mock_ota = Mock()
        aws_manager.set_ota_manager(mock_ota)
        assert aws_manager.ota_manager == mock_ota

    def test_initialize_ota_capabilities_success(self, aws_manager):
        """Test successful OTA capabilities initialization."""
        mock_ota = Mock()
        mock_ota.initialize_jobs_client.return_value = True
        aws_manager.ota_manager = mock_ota
        aws_manager.connected = True

        result = aws_manager.initialize_ota_capabilities()
        assert result is True
        mock_ota.initialize_jobs_client.assert_called_once()

    def test_initialize_ota_capabilities_not_connected(self, aws_manager):
        """Test OTA initialization when not connected."""
        mock_ota = Mock()
        aws_manager.ota_manager = mock_ota
        aws_manager.connected = False

        result = aws_manager.initialize_ota_capabilities()
        assert result is False
        mock_ota.initialize_jobs_client.assert_not_called()

    def test_initialize_ota_capabilities_no_manager(self, aws_manager):
        """Test OTA initialization without OTA manager."""
        aws_manager.connected = True

        result = aws_manager.initialize_ota_capabilities()
        assert result is False