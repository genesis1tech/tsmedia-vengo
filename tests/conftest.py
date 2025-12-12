"""
Test configuration and shared fixtures for TSV6 test suite.
"""
import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock


@pytest.fixture
def mock_hardware():
    """Mock all hardware interfaces for CI testing."""
    with patch('board.SCL'), \
         patch('board.SDA'), \
         patch('busio.I2C'), \
         patch('adafruit_pca9685.PCA9685'):
        yield


@pytest.fixture
def temp_config_dir(tmp_path):
    """Temporary config directory with dummy certificates for tests."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Create dummy certificates
    (config_dir / "aws_cert_crt.pem").write_text("DUMMY CERTIFICATE CONTENT")
    (config_dir / "aws_cert_private.pem").write_text("DUMMY PRIVATE KEY CONTENT")
    (config_dir / "aws_cert_ca.pem").write_text("DUMMY CA CERTIFICATE CONTENT")

    return config_dir


@pytest.fixture
def mock_aws_iot_client():
    """Mock AWS IoT client for testing."""
    mock_client = MagicMock()
    mock_client.connect.return_value = True
    mock_client.disconnect.return_value = True
    mock_client.publish.return_value = True
    mock_client.subscribe.return_value = True
    return mock_client


@pytest.fixture
def mock_network_interfaces():
    """Mock network interfaces for testing."""
    with patch('psutil.net_if_addrs') as mock_if_addrs, \
         patch('psutil.net_if_stats') as mock_if_stats:

        # Mock network addresses
        mock_addr = Mock()
        mock_addr.address = "192.168.1.100"
        mock_addr.netmask = "255.255.255.0"
        mock_if_addrs.return_value = {"wlan0": [mock_addr]}

        # Mock interface stats
        mock_stat = Mock()
        mock_stat.isup = True
        mock_if_stats.return_value = {"wlan0": mock_stat}

        yield


@pytest.fixture
def mock_gpio():
    """Mock GPIO operations for testing."""
    with patch('gpiozero.DigitalOutputDevice') as mock_output, \
         patch('gpiozero.DigitalInputDevice') as mock_input:

        mock_output_instance = Mock()
        mock_input_instance = Mock()
        mock_input_instance.value = 1

        mock_output.return_value = mock_output_instance
        mock_input.return_value = mock_input_instance

        yield


@pytest.fixture
def mock_servo_controller():
    """Mock servo controller for testing."""
    with patch('adafruit_pca9685.PCA9685') as mock_pca, \
         patch('adafruit_motor.servo.Servo') as mock_servo:

        mock_pca_instance = Mock()
        mock_servo_instance = Mock()
        mock_servo_instance.angle = None

        mock_pca.return_value = mock_pca_instance
        mock_servo.return_value = mock_servo_instance

        yield


@pytest.fixture
def sample_barcode_data():
    """Sample barcode data for testing."""
    return {
        "valid_barcode": "123456789012",
        "invalid_barcode": "INVALID",
        "empty_barcode": "",
        "long_barcode": "A" * 50
    }


@pytest.fixture
def sample_aws_payload():
    """Sample AWS IoT payload for testing."""
    return {
        "timestamp": "2024-01-01T12:00:00Z",
        "device_id": "TS-12345678",
        "barcode": "123456789012",
        "action": "validate",
        "response": {
            "valid": True,
            "product_info": {
                "name": "Test Product",
                "category": "Test Category"
            }
        }
    }


@pytest.fixture
def mock_display():
    """Mock pygame display for testing."""
    with patch('pygame.display.set_mode') as mock_set_mode, \
         patch('pygame.display.flip') as mock_flip, \
         patch('pygame.image.load') as mock_load:

        mock_surface = Mock()
        mock_set_mode.return_value = mock_surface
        mock_load.return_value = Mock()

        yield mock_surface


@pytest.fixture
def mock_vlc():
    """Mock VLC media player for testing."""
    with patch('vlc.MediaPlayer') as mock_player:
        mock_instance = Mock()
        mock_instance.play.return_value = True
        mock_instance.stop.return_value = True
        mock_instance.pause.return_value = True
        mock_player.return_value = mock_instance

        yield mock_instance