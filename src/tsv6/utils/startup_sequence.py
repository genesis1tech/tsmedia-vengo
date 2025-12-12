import time
import socket
import subprocess
import logging
from pathlib import Path
from config import config
from aws_resilient_manager import ResilientAWSManager, RetryConfig
from config_display import display_device_config

logger = logging.getLogger(__name__)

def check_wifi_connection():
    """Check if WiFi is connected and return connection status."""
    try:
        # Try to connect to a reliable host (Google DNS)
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        return True
    except OSError:
        return False

def get_wifi_info():
    """Get current WiFi connection information."""
    try:
        # Get WiFi interface info using iwconfig (Linux)
        result = subprocess.run(['iwconfig'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.split('\n')
            for line in lines:
                if 'ESSID:' in line and 'off/any' not in line:
                    essid = line.split('ESSID:')[1].strip().strip('"')
                    return essid
    except:
        pass
    
    # Fallback: try to get hostname/IP
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        return f"Connected ({local_ip})"
    except:
        return "Unknown"

def check_pigpio_daemon():
    """Check if pigpio daemon is running."""
    try:
        result = subprocess.run(['pgrep', 'pigpiod'], capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

def start_pigpio_daemon():
    """Start the pigpio daemon if not already running."""
    try:
        # Check if already running
        if check_pigpio_daemon():
            logger.info("pigpio daemon already running")
            return True

        logger.info("Starting pigpio daemon...")
        # Try to start pigpio daemon
        result = subprocess.run(['sudo', 'pigpiod'], capture_output=True, text=True, timeout=10)

        # Give it a moment to start
        time.sleep(2)

        # Verify it started
        if check_pigpio_daemon():
            logger.info("pigpio daemon started successfully")
            return True
        else:
            logger.error("Failed to start pigpio daemon")
            return False

    except subprocess.TimeoutExpired:
        logger.error("pigpio daemon startup timed out")
        return False
    except Exception as e:
        logger.error(f"Error starting pigpio daemon: {e}")
        return False

def pigpio_daemon_sequence(display_manager):
    """Execute pigpio daemon startup with retries."""
    logger.info("Step 3: Starting pigpio daemon...")

    pigpio_started = False
    for attempt in range(1, 6):  # 5 attempts for pigpio
        logger.info(f"pigpio daemon attempt {attempt}/5")

        if start_pigpio_daemon():
            logger.info("pigpio daemon is running")
            pigpio_started = True
            time.sleep(2)  # Show success message briefly
            break
        else:
            logger.error(f"pigpio daemon attempt {attempt} failed")

            if attempt < 5:  # Don't wait after the last attempt
                time.sleep(5)  # 5 second delay between retries

    if not pigpio_started:
        logger.error("pigpio daemon failed after 5 attempts - CRITICAL ERROR")
        time.sleep(5)
        return False  # Return False - this is critical for servo operation

    return True

def wifi_connection_sequence(display_manager):
    """Execute WiFi connection check with retries."""
    logger.info("Step 1: Checking WiFi connection...")

    wifi_connected = False
    for attempt in range(1, 6):  # 5 attempts
        logger.info(f"WiFi connection attempt {attempt}/5")

        if check_wifi_connection():
            wifi_info = get_wifi_info()
            logger.info(f"WiFi connected: {wifi_info}")
            wifi_connected = True
            time.sleep(2)  # Show success message briefly
            break
        else:
            logger.error(f"WiFi connection attempt {attempt} failed")

            if attempt < 5:  # Don't wait after the last attempt
                time.sleep(5)

    if not wifi_connected:
        logger.error("WiFi connection failed after 5 attempts")
        time.sleep(5)
        return False

    return True

def aws_connection_sequence(display_manager):
    """Execute AWS connection with retries."""
    logger.info("Step 2: Connecting to AWS IoT...")

    # Initialize Resilient AWS Manager
    cert_dir = config.files.CERTS_DIR
    retry_config = RetryConfig(
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=30.0,
        backoff_multiplier=2.0
    )

    aws_manager = ResilientAWSManager(
        thing_name=config.device.thing_name,
        endpoint=config.aws.IOT_ENDPOINT,
        cert_path=str(cert_dir / "aws_cert_crt.pem"),
        key_path=str(cert_dir / "aws_cert_private.pem"),
        ca_path=str(cert_dir / "aws_cert_ca.pem"),
        retry_config=retry_config,
        use_unique_client_id=True  # Enable unique client IDs to prevent DUPLICATE_CLIENTID errors
    )

    aws_connected = False
    for attempt in range(1, 6):  # 5 attempts
        logger.info(f"AWS connection attempt {attempt}/5")

        if aws_manager.connect():
            logger.info(f"AWS IoT connected: {config.aws.IOT_ENDPOINT}")
            aws_connected = True
            time.sleep(2)  # Show success message briefly
            break
        else:
            logger.error(f"AWS connection attempt {attempt} failed")

            if attempt < 5:  # Don't wait after the last attempt
                time.sleep(5)

    if not aws_connected:
        logger.error("AWS connection failed after 5 attempts")
        time.sleep(5)
        return None

    return aws_manager

def servo_initialization_sequence(servo_controller):
    """Execute servo initialization to closed position (0 degrees)."""
    logger.info("Step 4: Initializing servo to closed position...")
    
    if servo_controller is None:
        logger.warning("Servo controller not available - skipping servo initialization")
        return True
    
    try:
        servo_controller._set_angle(0)
        time.sleep(0.5)  # Allow servo to settle
        
        # Disable servo pulses to prevent jitter and save power when at rest
        servo_controller.disable_servo()
        
        logger.info("Servo initialized to closed position (0°) - pulses disabled")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize servo: {e}")
        return False

def display_ready_screen(display_manager):
    """Display the device ready screen with green background."""
    logger.info("Step 6: System ready - showing green 'Device Ready' screen")
    # Show green screen with "Device Ready" text
    display_manager.show_device_ready()

    logger.info("System ready - Device Ready displayed")

def execute_startup_sequence(display_manager, device_manager=None, servo_controller=None):
    """
    Execute the complete startup sequence.

    Args:
        display_manager: Display manager instance
        device_manager: Device manager instance (optional)
        servo_controller: Servo controller instance (optional)

    Returns:
        tuple: (ResilientAWSManager instance, pigpio_available) if successful, (None, False) if failed
        tuple: (AWSManager instance, pigpio_available) if successful, (None, False) if failed
    """
    logger.info("Executing startup sequence...")

    # Clear the screen to black at the start
    display_manager.clear_screen((0, 0, 0))

    # Step 1: WiFi connection with retries
    if not wifi_connection_sequence(display_manager):
        return None, False

    # Step 2: AWS connection with retries
    aws_manager = aws_connection_sequence(display_manager)
    if aws_manager is None:
        return None, False

    # Step 3: Start pigpio daemon (CRITICAL - must succeed)
    if not pigpio_daemon_sequence(display_manager):
        return None, False  # Exit if pigpio fails - it's required for servo operation
    
    # Step 4: Initialize servo to closed position
    if not servo_initialization_sequence(servo_controller):
        logger.warning("Servo initialization failed - continuing startup")
    
    # Step 5: Display device configuration with 2.5 second delay
    logger.info("Step 5: Displaying device configuration...")
    display_device_config(display_manager)
    
    # Step 6: Display ready screen (green background with "Device Ready")
    display_ready_screen(display_manager)

    return aws_manager, True