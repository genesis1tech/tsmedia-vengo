"""
Configuration Module for Topper Stopper Raspberry Pi
====================================================

This module centralizes all configuration settings, constants, and credentials
for the Topper Stopper device running on Raspberry Pi.

Usage:
    from config import Config, DisplayConfig, NetworkConfig, etc.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path

# Import version utility for dynamic version management
try:
    from tsv6.utils.version import get_firmware_version
except ImportError:
    # Fallback if version module not available during imports
    def get_firmware_version():
        return "6.0.0"


@dataclass
class DeviceConfig:
    """Device-specific configuration settings"""
    DEVICE_TYPE: str = "Topper Stopper V5 Raspberry Pi"
    FIRMWARE_VERSION: str = field(default_factory=get_firmware_version)  # Dynamically read from pyproject.toml
    DEVICE_CLIENT: str = "Genesis 1 Technologies LLC"
    DEVICE_LOCATION: str = "Demo Unit"
    WARRANTY_START_DATE: str = "2024-09-11"
    WARRANTY_END_DATE: str = "2025-09-11"

    # Device naming
    THING_NAME_PREFIX: str = "TS_"

    @property
    def device_id(self) -> str:
        """Generate device ID from Raspberry Pi serial number"""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        serial = line.split(':')[1].strip()
                        return serial[-8:].upper()  # Last 8 characters
        except:
            pass
        return "0000"  # Fallback

    @property
    def thing_name(self) -> str:
        """Generate AWS IoT Thing name"""
        return f"{self.THING_NAME_PREFIX}{self.device_id}"


@dataclass
class DisplayConfig:
    """Display hardware configuration"""
    # Screen dimensions (adjust for your specific display)
    SCREEN_WIDTH: int = 800
    SCREEN_HEIGHT: int = 480

    # Display settings
    BRIGHTNESS: int = 255
    ROTATION: int = 0  # 0, 90, 180, 270 degrees

    # Image cycling settings
    IMAGE_CYCLE_INTERVAL: int = 5000  # milliseconds

    # QR Code display settings
    QR_SCALE: int = 5
    QR_MARGIN: int = 5
    QR_X_OFFSET: int = 33
    QR_Y_OFFSET_FROM_BOTTOM: int = 446
    QR_VERSION: int = 6  # QR code version for transaction URLs

    # Product image display settings
    product_image_background_color: str = "white"

    # Colors (RGB tuples)
    COLOR_BLACK: tuple = (0, 0, 0)
    COLOR_WHITE: tuple = (255, 255, 255)
    COLOR_RED: tuple = (255, 0, 0)


@dataclass
class NetworkConfig:
    """Network and connectivity settings"""
    # WiFi settings
    WIFI_TIMEOUT: int = 10000  # Connection timeout in ms
    WIFI_CHECK_INTERVAL: int = 30000  # Health check interval
    WIFI_MAX_RECONNECT_ATTEMPTS: int = 3

    # Access Point settings for setup mode
    AP_NAME_PREFIX: str = "TS_"
    AP_PASSWORD: str = "recycleit"
    AP_TIMEOUT: int = 180  # seconds

    # Signal strength thresholds
    WIFI_WEAK_SIGNAL_THRESHOLD: int = -80  # dBm


@dataclass
class AWSConfig:
    """AWS IoT configuration"""
    # AWS IoT Core settings
    IOT_ENDPOINT: str = "a13t5p0hkhkxql-ats.iot.us-east-1.amazonaws.com"
    IOT_PORT: int = 8883

    # Connection settings
    CONNECTION_TIMEOUT: int = 10000  # milliseconds
    MAX_RECONNECT_ATTEMPTS: int = 3
    KEEP_ALIVE: int = 60  # seconds

    # Topic templates (will be formatted with thing_name)
    SHADOW_UPDATE_TOPIC: str = "$aws/things/{thing_name}/shadow/update"
    OPEN_DOOR_TOPIC: str = "{thing_name}/openDoor"
    NO_MATCH_TOPIC: str = "{thing_name}/noMatch"
    QR_CODE_TOPIC: str = "{thing_name}/qrCode"

    # Certificate paths (relative to project root)
    CERT_CA_PATH: str = "certs/aws_cert_ca.pem"
    CERT_CRT_PATH: str = "certs/aws_cert_crt.pem"
    CERT_PRIVATE_PATH: str = "certs/aws_cert_private.pem"


@dataclass
class ScannerConfig:
    """Barcode scanner configuration"""
    # Serial port settings (adjust for your setup)
    SERIAL_PORT: str = "/dev/ttyUSB0"  # Common for USB-to-serial adapters
    BAUD_RATE: int = 9600
    TIMEOUT: int = 1  # seconds

    # Scanner behavior
    SCAN_DEBOUNCE_TIME: int = 50  # milliseconds
    SCAN_TIMEOUT: int = 5000  # milliseconds to wait for complete scan


@dataclass
class ServoConfig:
    """Servo motor configuration (legacy - for DFRobot HAT)"""
    # DFRobot HAT PWM channel (0-3)
    SERVO_PIN: int = 0  # DFRobot HAT PWM 0

    # Servo positions (in degrees or PWM values)
    POSITION_CLOSED: int = 0
    POSITION_OPEN: int = 68

    # Timing
    DOOR_OPEN_DURATION: int = 3000  # milliseconds
    SERVO_SETTLE_TIME: int = 500  # milliseconds


@dataclass
class BusServoConfig:
    """
    Waveshare ST3020 Bus Servo configuration.

    Used with Bus Servo Adapter (A) via USB serial.
    Position values: 0-4095 (4096 steps per 360 degrees)
    """
    # Driver type
    driver: str = "stservo"

    # Serial port settings
    port: str = "/dev/ttyUSB0"
    baudrate: int = 115200

    # Servo identification
    servo_id: int = 1

    # Position settings (0-4095 range)
    # 120 degrees = 1365, 90 degrees = 1024
    open_position: int = 1365   # 120 degrees
    closed_position: int = 0    # 0 degrees

    # Speed and timing
    moving_speed: int = 0       # 0 = maximum speed (fastest)
    acceleration: int = 50      # Acceleration value
    hold_seconds: float = 3.0   # Time to hold door open
    timeout_seconds: float = 1.0  # Command timeout

    def degrees_to_position(self, degrees: float) -> int:
        """Convert degrees to position value."""
        return int(degrees * 4096.0 / 360.0)

    def position_to_degrees(self, position: int) -> float:
        """Convert position value to degrees."""
        return position * 360.0 / 4096.0


@dataclass
class FileConfig:
    """File system and storage configuration"""
    # Base directories
    BASE_DIR: Path = Path(__file__).parent
    DATA_DIR: Path = BASE_DIR / "data"
    IMAGES_DIR: Path = BASE_DIR / "images"
    LOGS_DIR: Path = BASE_DIR / "logs"
    CERTS_DIR: Path = Path(__file__).parent.parent.parent.parent / "assets" / "certs"

    # Product database
    PRODUCTS_FILE: str = "products.json"
    PRODUCTS_VERSION_FILE: str = "products_version.txt"

    # Logging
    LOG_FILE: str = "topper_stopper.log"
    LOG_MAX_SIZE: int = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT: int = 5

    def __post_init__(self):
        """Initialize directories"""
        # Ensure directories exist
        for directory in [self.DATA_DIR, self.IMAGES_DIR, self.LOGS_DIR, self.CERTS_DIR]:
            directory.mkdir(parents=True, exist_ok=True)


@dataclass
class ProductConfig:
    """Product management configuration"""
    # S3 bucket settings for product database
    S3_BUCKET_URL: str = "https://your-bucket.s3.amazonaws.com/products.json"
    S3_VERSION_URL: str = "https://your-bucket.s3.amazonaws.com/products_version.txt"

    # Local cache settings
    CACHE_EXPIRY_HOURS: int = 24
    MAX_CACHE_SIZE_MB: int = 50

    # Product lookup settings
    LOOKUP_TIMEOUT: int = 5000  # milliseconds
    RETRY_ATTEMPTS: int = 3


@dataclass
class TaskConfig:
    """Task scheduling and timing configuration"""
    # Task intervals (in seconds)
    STATUS_PUBLISH_INTERVAL: int = 60  # 60 seconds
    IMAGE_CYCLE_CHECK_INTERVAL: float = 0.01  # 10ms
    SCANNER_CHECK_INTERVAL: float = 0.01  # 10ms
    NETWORK_CHECK_INTERVAL: int = 30

    # Task priorities and settings
    HIGH_PRIORITY_TASKS: List[str] = None
    MEDIUM_PRIORITY_TASKS: List[str] = None
    LOW_PRIORITY_TASKS: List[str] = None

    def __post_init__(self):
        """Initialize task priority lists"""
        if self.HIGH_PRIORITY_TASKS is None:
            self.HIGH_PRIORITY_TASKS = ["barcode_scanning", "servo_control"]

        if self.MEDIUM_PRIORITY_TASKS is None:
            self.MEDIUM_PRIORITY_TASKS = ["display_management", "network_monitoring"]

        if self.LOW_PRIORITY_TASKS is None:
            self.LOW_PRIORITY_TASKS = ["status_publishing", "image_cycling"]


@dataclass
class MDashConfig:
    """mDash configuration"""
    APP_NAME: str = "TopperStopper_v5_raspberry_pi"
    DEVICE_PASSWORD: str = "SsSy7eTW0muxW1l7g0R5Fg"  # device7

    # mDash endpoints and settings
    MDASH_SERVER: str = "mdash.net"
    MDASH_PORT: int = 443


@dataclass
class SecurityConfig:
    """Security and encryption settings"""
    # Environment variable names for sensitive data
    AWS_ACCESS_KEY_ENV: str = "AWS_ACCESS_KEY_ID"
    AWS_SECRET_KEY_ENV: str = "AWS_SECRET_ACCESS_KEY"
    WIFI_PASSWORD_ENV: str = "WIFI_PASSWORD"

    # Certificate validation
    VERIFY_SSL: bool = True

    # API keys and tokens (use environment variables in production)
    API_TIMEOUT: int = 30  # seconds



@dataclass
class VideoConfig:
    """Video playback configuration for VLC optimization"""
    # Hardware acceleration settings
    hardware_acceleration: bool = True  # Enable hardware decoding
    hardware_acceleration_fallback: bool = True  # Fallback to software if HW fails
    
    # Caching settings (in milliseconds)
    file_caching_ms: int = 2000  # 2 seconds for SD card latency
    network_caching_ms: int = 300  # 300ms for network streams
    
    # Video output settings
    vout_mode: str = "x11"  # X11 output for tkinter embedding (drm bypasses X11)
    
    # MediaListPlayer settings
    use_medialist_player: bool = True  # Use playlist for seamless transitions
    preload_decoder: bool = True  # Preload first video to warm up decoder
    
    # Performance settings
    video_status_check_interval_ms: int = 5000  # 5 seconds (reduced from 2s)
    
    # Additional VLC optimization flags
    disable_video_title: bool = True
    disable_stats: bool = True
    disable_snapshot_preview: bool = True
    disable_screensaver: bool = True
    clock_jitter: int = 0
    clock_synchro: int = 0


@dataclass
class OTAConfig:
    """OTA (Over-The-Air) update configuration"""
    # OTA feature enablement
    ENABLED: bool = True
    AUTO_INSTALL: bool = True  # Automatically install updates when received
    
    # Staging directories
    STAGING_DIR: str = "/tmp/ota_staging"
    BACKUP_DIR: str = "/tmp/ota_backup"
    MEDIA_STAGING_DIR: str = "/tmp/media_staging"
    
    # Security settings
    VERIFY_SIGNATURES: bool = True
    REQUIRE_HTTPS: bool = True
    MAX_DOWNLOAD_SIZE_MB: int = 500  # Maximum update package size
    
    # Update policies
    MAX_CONCURRENT_DOWNLOADS: int = 3
    DOWNLOAD_TIMEOUT_SECONDS: int = 300  # 5 minutes
    MAX_RETRY_ATTEMPTS: int = 3
    RETRY_DELAY_SECONDS: int = 60
    
    # Backup retention
    BACKUP_RETENTION_DAYS: int = 7
    MAX_BACKUP_SIZE_MB: int = 1000
    
    # Restart settings
    DEFAULT_RESTART_DELAY: int = 10  # Seconds to wait before restart
    FORCE_RESTART_TIMEOUT: int = 60  # Force restart if graceful fails
    
    # Media update settings
    MEDIA_DIRS: List[str] = None  # Will be populated in __post_init__
    SUPPORTED_MEDIA_FORMATS: List[str] = None
    MAX_MEDIA_FILE_SIZE_MB: int = 100
    
    # Progress reporting
    PROGRESS_REPORT_INTERVAL: int = 5  # Report progress every N seconds
    STATUS_REPORT_TIMEOUT: int = 30
    
    # AWS IoT Jobs settings
    JOBS_POLL_INTERVAL: int = 60  # Check for new jobs every N seconds
    JOBS_EXECUTION_TIMEOUT: int = 3600  # 1 hour max execution time
    
    def __post_init__(self):
        """Initialize default values"""
        if self.MEDIA_DIRS is None:
            self.MEDIA_DIRS = [
                "assets/videos",
                "assets/images", 
                "event_images",
                "media"
            ]
            
        if self.SUPPORTED_MEDIA_FORMATS is None:
            self.SUPPORTED_MEDIA_FORMATS = [
                # Video formats
                ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm",
                # Image formats  
                ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
                # Audio formats
                ".mp3", ".wav", ".ogg", ".m4a", ".flac"
            ]


@dataclass
class ProvisioningConfig:
    """WiFi provisioning configuration for first-boot setup"""
    # Feature toggle
    enabled: bool = True

    # Timing
    timeout_seconds: int = 600  # 10 minutes
    connection_test_timeout: int = 30
    max_connection_retries: int = 3

    # Access Point settings
    ap_interface: str = "wlan0"
    ap_ip: str = "192.168.4.1"
    ap_netmask: str = "255.255.255.0"
    ap_dhcp_start: str = "192.168.4.2"
    ap_dhcp_end: str = "192.168.4.20"
    ap_ssid_prefix: str = "TS_"
    ap_password: str = "recycleit"
    ap_channel: int = 7

    # Web server
    web_port: int = 80

    # Paths
    wpa_supplicant_conf: str = "/etc/wpa_supplicant/wpa_supplicant.conf"
    hostapd_conf: str = "/tmp/hostapd_provisioning.conf"
    dnsmasq_conf: str = "/tmp/dnsmasq_provisioning.conf"


@dataclass
class LTEConfig:
    """
    SIM7600NA-H 4G LTE HAT configuration.

    Optimized for Hologram.io as the service provider.
    Reference: https://www.waveshare.com/wiki/SIM7600NA-H_4G_HAT
    """
    # Feature toggle
    enabled: bool = False

    # Serial port settings
    port: str = ""  # Auto-detect if empty
    baudrate: int = 115200

    # Hologram.io APN settings (no authentication required)
    apn: str = "hologram"
    apn_username: str = ""
    apn_password: str = ""

    # Network preferences
    force_lte: bool = True  # Use AT+CNMP=38 to force LTE mode
    enable_roaming: bool = True  # Required for Hologram global SIM
    rndis_mode: bool = True  # Use RNDIS USB network interface

    # GPIO for Raspberry Pi power control (GPIO D6 = BCM 6)
    power_gpio: int = 6
    use_gpio_power: bool = True

    # Monitoring settings
    check_interval_secs: float = 30.0
    signal_weak_threshold: int = 10  # CSQ value (0-31)
    signal_critical_threshold: int = 5
    keepalive_interval_secs: int = 30

    # Recovery thresholds
    soft_recovery_threshold: int = 2
    intermediate_recovery_threshold: int = 4
    hard_recovery_threshold: int = 6
    critical_escalation_threshold: int = 10


@dataclass
class ConnectivityConfig:
    """
    Network connectivity mode configuration.

    Supports WiFi/LTE failover with configurable primary/backup modes.
    """
    # Connectivity mode: wifi_only, lte_only, wifi_primary_lte_backup, lte_primary_wifi_backup
    mode: str = "lte_primary_wifi_backup"

    # Failover timing
    failover_timeout_secs: float = 60.0  # Time before switching to backup
    failback_check_interval_secs: float = 300.0  # How often to check if primary recovered
    failback_stability_secs: float = 30.0  # Primary must be stable before switching back

    # Status reporting
    status_report_interval_secs: float = 60.0  # 60 seconds


@dataclass
class PiSignageLocalConfig:
    """
    Local PiSignage feature toggle and settings that don't belong in the
    adapter's frozen config (which reads directly from env vars).

    The full connection config lives in ``tsv6.display.pisignage_adapter.PiSignageConfig``.
    """
    # Feature toggle — set False to fall back to VLC-based EnhancedVideoPlayer
    enabled: bool = field(
        default_factory=lambda: os.environ.get("PISIGNAGE_ENABLED", "false").lower() == "true"
    )


class Config:
    """Main configuration class that combines all config sections"""

    def __init__(self):
        self.device = DeviceConfig()
        self.display = DisplayConfig()
        self.network = NetworkConfig()
        self.aws = AWSConfig()
        self.scanner = ScannerConfig()
        self.servo = ServoConfig()
        self.bus_servo = BusServoConfig()
        self.files = FileConfig()
        self.products = ProductConfig()
        self.tasks = TaskConfig()
        self.mdash = MDashConfig()
        self.security = SecurityConfig()
        self.video = VideoConfig()
        self.ota = OTAConfig()
        self.provisioning = ProvisioningConfig()
        self.lte = LTEConfig()
        self.connectivity = ConnectivityConfig()
        self.pisignage = PiSignageLocalConfig()

    def get_aws_topics(self) -> dict:
        """Get formatted AWS IoT topics for this device"""
        thing_name = self.device.thing_name
        return {
            'shadow_update': self.aws.SHADOW_UPDATE_TOPIC.format(thing_name=thing_name),
            'open_door': self.aws.OPEN_DOOR_TOPIC.format(thing_name=thing_name),
            'no_match': self.aws.NO_MATCH_TOPIC.format(thing_name=thing_name),
            'qr_code': self.aws.QR_CODE_TOPIC.format(thing_name=thing_name)
        }

    def get_wifi_ap_name(self) -> str:
        """Get WiFi access point name for setup mode"""
        return f"{self.network.AP_NAME_PREFIX}{self.device.device_id}"

    def validate_config(self) -> List[str]:
        """Validate configuration and return list of issues"""
        issues = []

        # Check certificate files exist
        cert_files = [
            self.files.CERTS_DIR / "aws_cert_ca.pem",
            self.files.CERTS_DIR / "aws_cert_crt.pem", 
            self.files.CERTS_DIR / "aws_cert_private.pem"
        ]

        for cert_file in cert_files:
            if not cert_file.exists():
                issues.append(f"Certificate file missing: {cert_file}")

        # Check image directories
        if not self.files.IMAGES_DIR.exists():
            issues.append(f"Images directory missing: {self.files.IMAGES_DIR}")

        # Validate network settings
        if not self.aws.IOT_ENDPOINT:
            issues.append("AWS IoT endpoint not configured")

        return issues

    def print_summary(self):
        """Print configuration summary"""
        print("=== Topper Stopper Configuration Summary ===")
        print(f"Device ID: {self.device.device_id}")
        print(f"Thing Name: {self.device.thing_name}")
        print(f"Firmware Version: {self.device.FIRMWARE_VERSION}")
        print(f"AWS Endpoint: {self.aws.IOT_ENDPOINT}")
        print(f"Display Size: {self.display.SCREEN_WIDTH}x{self.display.SCREEN_HEIGHT}")
        print(f"Scanner Port: {self.scanner.SERIAL_PORT}")
        print(f"Servo Pin: {self.servo.SERVO_PIN}")
        print("=" * 45)


# Global configuration instance
config = Config()

# Convenience exports
__all__ = [
    'Config',
    'DeviceConfig',
    'DisplayConfig',
    'NetworkConfig',
    'AWSConfig',
    'ScannerConfig',
    'ServoConfig',
    'BusServoConfig',
    'FileConfig',
    'ProductConfig',
    'TaskConfig',
    'MDashConfig',
    'SecurityConfig',
    'VideoConfig',
    'OTAConfig',
    'ProvisioningConfig',
    'LTEConfig',
    'ConnectivityConfig',
    'PiSignageLocalConfig',
    'config'
]


