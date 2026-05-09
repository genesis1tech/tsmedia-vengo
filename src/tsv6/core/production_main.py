#!/usr/bin/env python3
"""
Production-Ready TSV6 Video Player

Enhanced version with comprehensive error handling, monitoring, and recovery
for production IoT deployment. Includes:

- Network monitoring and WiFi stability with staged recovery
- AWS connection resilience with retry logic  
- System health monitoring with escalation
- Enhanced error recovery system with persistent failure tracking
- Production configuration management
- Comprehensive logging
"""

import sys
import os
import threading
import time
import logging
import logging.config
import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Core imports
from tsv6.core.main import EnhancedVideoPlayer, OptimizedBarcodeScanner
from tsv6.core.aws_resilient_manager import ResilientAWSManager, RetryConfig
from tsv6.ota.ota_manager import OTAManager
from tsv6.utils.network_monitor import NetworkMonitor, NetworkMonitorConfig
from tsv6.utils.systemd_recovery_manager import SystemdRecoveryManager
from tsv6.utils.lte_monitor import LTEMonitor, LTEMonitorConfig
from tsv6.utils.connectivity_manager import ConnectivityManager, ConnectivityManagerConfig, ConnectivityMode
from tsv6.utils.health_monitor import HealthMonitor, HealthThresholds
from tsv6.utils.enhanced_health_monitor import EnhancedHealthMonitor
from tsv6.hardware.display_driver_monitor import DisplayDriverMonitor
from tsv6.utils.error_recovery import ErrorRecoverySystem, RecoveryAction, EscalationLevel
from tsv6.config.production_config import ProductionConfigManager
from tsv6.utils.memory_optimizer import MemoryOptimizer, MemoryThresholds, get_global_memory_optimizer
from tsv6.monitoring.watchdog_monitor import WatchdogMonitor
from tsv6.utils.connection_tracker import ConnectionTracker, ConnectionDeadlineMonitor
from tsv6.utils.splash_screen import SplashScreen
# SensorStatusIndicator / ConnectionStatusIndicator removed — they created
# extra Tk windows that conflicted with the VLC zone window and offered no
# user value at the screen positions used. WiFi access is now via the 5s
# long-press gesture or the LXDE root-window menu when the kiosk is stopped.


def env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable with a safe default."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return default


def env_int(name: str, default: int, minimum: int = 1) -> int:
    """Parse an integer environment variable with a lower bound."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= minimum else default

# Sleep mode imports
# Removed for memory-fix branch

try:
    from tsv6.hardware.stservo import STServoController
    SERVO_AVAILABLE = True
except ImportError:
    SERVO_AVAILABLE = False
    print("STServo controller not available")

try:
    from tsv6.hardware.sim7600 import SIM7600Controller, SIM7600Config
    SIM7600_AVAILABLE = True
except ImportError:
    SIM7600_AVAILABLE = False
    print("SIM7600 LTE controller not available")

try:
    from tsv6.hardware.tof_sensor import ToFSensor, ToFSensorConfig
    from tsv6.utils.bin_level_monitor import BinLevelMonitor, BinLevelMonitorConfig
    TOF_SENSOR_AVAILABLE = True
except ImportError:
    TOF_SENSOR_AVAILABLE = False
    print("ToF sensor module not available")

try:
    from tsv6.hardware.recycle_sensor import RecycleSensor, RecycleSensorConfig, SensorState
    RECYCLE_SENSOR_AVAILABLE = True
except ImportError:
    RECYCLE_SENSOR_AVAILABLE = False
    print("Recycle verification sensor not available")

# PiSignage display adapter (remote server on Hostinger)
try:
    from tsv6.display.pisignage_adapter import PiSignageAdapter, PiSignageConfig
    from tsv6.display.pisignage_health import PiSignageHealthMonitor
    from tsv6.display.playlist_manager import PlaylistManager
    PISIGNAGE_AVAILABLE = True
except ImportError:
    PISIGNAGE_AVAILABLE = False
    print("PiSignage display adapter not available")

# TSV6 native in-process player (no separate PiSignage player software required)
try:
    from tsv6.display.tsv6_player.backend import TSV6NativeBackend
    TSV6_NATIVE_AVAILABLE = True
except ImportError:
    TSV6_NATIVE_AVAILABLE = False
    print("TSV6 native backend not available")

# Touchscreen long-press gesture (evdev-level, bypasses Chromium DOM)
try:
    from tsv6.display.tsv6_player.touch_gesture import LongPressWatcher
    LONGPRESS_AVAILABLE = True
except ImportError:
    LONGPRESS_AVAILABLE = False


class ProductionVideoPlayer:
    """Production-ready video player with enhanced monitoring and recovery"""
    
    def __init__(self):
        print("=" * 80)
        print("🏭 TSV6 Production Video Player - Starting (Enhanced Recovery)")
        print("=" * 80)
        
        # Initialize production configuration
        self.config_manager = ProductionConfigManager()
        self.aws_config = self.config_manager.get_aws_config()
        
        # Setup logging
        self._setup_logging()
        self.logger = logging.getLogger(__name__)
        self.logger.info("Starting TSV6 Production Video Player with Enhanced Recovery")
        
        # Initialize enhanced monitoring and recovery systems
        self.error_recovery = ErrorRecoverySystem()
        self.network_monitor = None
        self.health_monitor = None
        self.memory_optimizer = None
        self.aws_manager = None
        self.ota_manager = None
        self.video_player = None
        self.barcode_scanner = None
        self.servo_controller = None

        # LTE connectivity components
        self.lte_controller = None
        self.lte_monitor = None
        self.connectivity_manager = None

        # ToF bin level monitoring
        self.tof_sensor = None
        self.bin_level_monitor = None

        # Recycling verification sensor
        self.recycle_sensor = None
        # When False, the door sequence skips the ToF item-detection wait and
        # always treats the deposit as successful. Use for testing or for
        # deployments where verification happens elsewhere (e.g. paired with
        # the TS Rewards app). Toggle via TSV6_RECYCLE_VERIFICATION_REQUIRED.
        self._recycle_verification_required = os.environ.get(
            "TSV6_RECYCLE_VERIFICATION_REQUIRED", "true"
        ).lower() in ("true", "1", "yes")

        # PiSignage display adapter (remote server on Hostinger VPS)
        self.pisignage_adapter = None
        self.pisignage_health_monitor = None
        self._pisignage_enabled = False

        # Unified display backend (DisplayController).  Set by _initialize_pisignage
        # to either TSV6NativeBackend ("native") or PiSignageAdapter ("rest").
        # Remains None when PISIGNAGE_BACKEND=vlc or backend init fails, in which
        # case the legacy video_player path is used throughout.
        self.display_backend = None

        # Door sequence transaction guard — prevents concurrent/looping door operations
        self._door_sequence_active = False
        self._door_sequence_lock = threading.Lock()

        # Long-press touchscreen gesture watcher (evdev-level; starts in start())
        self._long_press_watcher = None

        # Network reconnects can leave Chromium on the local ready screen while
        # the Vengo iframe was loaded during an offline window. Debounce the
        # idle restart so repeated NM events do not thrash the display.
        self._vengo_reconnect_lock = threading.Lock()
        self._last_vengo_reconnect_restart_at = 0.0

        # Cloud-supplied playlist override for the recycle-sensor timeout path.
        # Set from the openDoor payload's noItemPlaylist field at door-sequence
        # start; consumed by _handle_recycle_failure when the sensor times out.
        self._pending_no_item_playlist = None

        # Splash screen for LTE startup wait
        self.splash_screen = None
        
        # Sensor / connection status indicator overlays removed — see import note above.

        
        # Initialize systemd recovery manager first (needed for connection deadline monitor)
        self.systemd_recovery = SystemdRecoveryManager(
            interface=self.config_manager.network_config.wifi_interface
        )
        
        # Connection tracking and deadline monitoring (Issue #TS_538A7DD4)
        self.connection_tracker = ConnectionTracker()
        connection_deadline_minutes = env_int(
            "TSV6_CONNECTION_DEADLINE_MINUTES",
            30,
            minimum=1,
        )
        connection_deadline_force_reboot = env_bool(
            "TSV6_CONNECTION_DEADLINE_FORCE_REBOOT",
            True,
        )
        self.connection_deadline_monitor = ConnectionDeadlineMonitor(
            disconnection_deadline_minutes=connection_deadline_minutes,
            check_interval_seconds=60,
            on_deadline_exceeded=self._on_connection_deadline_exceeded,
            enable_forced_reboot=connection_deadline_force_reboot,
            systemd_recovery_manager=self.systemd_recovery,  # CRITICAL FIX: Pass recovery manager
            connection_name="AWS IoT",
            reboot_reason="AWS IoT connection deadline exceeded",
        )

        network_failure_reboot_minutes = env_int(
            "TSV6_NETWORK_FAILURE_REBOOT_MINUTES",
            8,
            minimum=1,
        )
        network_failure_force_reboot = env_bool(
            "TSV6_NETWORK_FAILURE_FORCE_REBOOT",
            True,
        )
        self.network_deadline_monitor = ConnectionDeadlineMonitor(
            disconnection_deadline_minutes=network_failure_reboot_minutes,
            check_interval_seconds=30,
            on_deadline_exceeded=self._on_network_deadline_exceeded,
            enable_forced_reboot=network_failure_force_reboot,
            systemd_recovery_manager=self.systemd_recovery,
            connection_name="Network",
            reboot_reason="network unreachable past configured deadline",
        )
        
        # State tracking
        self.running = False
        self.shutdown_event = threading.Event()
        
        # Initialize all systems
        self._initialize_systems()
        
        # Register enhanced recovery handlers
        self._register_enhanced_recovery_handlers()
        
        self.logger.info("Enhanced production system initialization complete")
        
    def _setup_logging(self):
        """Setup production logging configuration"""
        try:
            logging_config = self.config_manager.get_logging_config()
            logging.config.dictConfig(logging_config)
            print("✅ Logging configured")
        except Exception as e:
            print(f"❌ Failed to setup logging: {e}")
            # Fallback to basic logging
            logging.basicConfig(level=logging.INFO)
    
    def _initialize_systems(self):
        """Initialize all system components"""
        self.logger.info("Initializing system components...")
        
        # Register components with enhanced error recovery
        self.error_recovery.register_component("network")
        self.error_recovery.register_component("aws_connection")
        self.error_recovery.register_component("video_player")
        self.error_recovery.register_component("barcode_scanner")
        self.error_recovery.register_component("servo_controller")
        self.error_recovery.register_component("ota_manager")
        self.error_recovery.register_component("system_health")
        self.error_recovery.register_component("memory_optimizer")
        self.error_recovery.register_component("lte_modem")
        self.error_recovery.register_component("tof_sensor")
        self.error_recovery.register_component("recycle_sensor")

        # Initialize LTE controller if enabled (before network monitor)
        self._initialize_lte_controller()

        # Initialize network monitoring with error recovery integration
        self._initialize_network_monitor()

        # Initialize LTE monitor (after LTE controller)
        self._initialize_lte_monitor()

        # Initialize connectivity manager (after both monitors)
        self._initialize_connectivity_manager()
        
        # Initialize memory optimizer (Priority: Critical for Issue #39)
        self._initialize_memory_optimizer()
        
        # Initialize health monitoring
        self._initialize_health_monitor()

        # Initialize ToF bin level sensor
        self._initialize_tof_sensor()

        # Initialize AWS manager
        self._initialize_aws_manager()
        
        # Initialize OTA manager
        self._initialize_ota_manager()
        
        # Initialize servo controller
        self._initialize_servo_controller()

        # Initialize recycle verification sensor (after servo, before video player)
        self._initialize_recycle_sensor()

        # Initialize PiSignage display adapter (if enabled)
        self._initialize_pisignage()

        # Initialize video player (skipped when PiSignage is active)
        self._initialize_video_player()
        
        # Initialize barcode scanner
        self._initialize_barcode_scanner()
        
        # Initialize watchdog monitor
        self._initialize_watchdog_monitor()
    
    def _initialize_watchdog_monitor(self):
        """Initialize watchdog monitoring"""
        try:
            self.watchdog_monitor = WatchdogMonitor()
            self.logger.info("Watchdog monitor initialized")
        except Exception as e:
            self.logger.warning(f"Failed to initialize watchdog monitor: {e}")
            self.watchdog_monitor = None
    
    def _initialize_network_monitor(self):
        """Initialize network monitoring with enhanced recovery integration"""
        try:
            # NetworkMonitor is observe-only (Layer 1). It does NOT perform recovery.
            # Recovery is handled by NetworkManager (Layer 0) and the shell watchdog (Layer 2).
            network_config = NetworkMonitorConfig(
                interface=self.config_manager.network_config.wifi_interface,
                check_interval_secs=10.0,
                weak_signal_threshold_dbm=-75,
            )
            
            self.network_monitor = NetworkMonitor(
                config=network_config,
                on_status=self._on_network_status,
                on_disconnect=self._on_network_disconnect,
                on_reconnect=self._on_network_reconnect,
                error_recovery_system=self.error_recovery,
            )

            self.logger.info("Network monitor initialized (observe-only, recovery via NM + shell watchdog)")
            self.error_recovery.report_success("network")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize network monitor: {e}")
            self.error_recovery.report_error("network", "initialization", str(e), "high")

    def _initialize_lte_controller(self):
        """Initialize SIM7600 LTE controller if enabled"""
        try:
            lte_config = self.config_manager.get_lte_config()

            if not lte_config.get("enabled", False):
                self.logger.info("LTE connectivity disabled in configuration")
                return

            if not SIM7600_AVAILABLE:
                self.logger.warning("SIM7600 module not available - LTE disabled")
                return

            self.logger.info("Initializing SIM7600 LTE controller...")

            config = SIM7600Config(
                port=lte_config.get("port") or None,
                baudrate=lte_config.get("baudrate", 115200),
                apn=lte_config.get("apn", "hologram"),
                apn_username=lte_config.get("apn_username", ""),
                apn_password=lte_config.get("apn_password", ""),
                force_lte=lte_config.get("force_lte", True),
                enable_roaming=lte_config.get("enable_roaming", True),
                rndis_mode=lte_config.get("rndis_mode", True),
                power_gpio=lte_config.get("power_gpio", 6),
                use_gpio_power=lte_config.get("use_gpio_power", True),
                keepalive_interval=lte_config.get("keepalive_interval_secs", 30),
            )

            self.lte_controller = SIM7600Controller(
                config=config,
                on_state_change=self._on_lte_state_change
            )

            if self.lte_controller.connect():
                self.logger.info("LTE controller connected successfully")
                self.error_recovery.report_success("lte_modem")
            else:
                self.logger.warning("LTE controller failed to connect - will retry")
                self.error_recovery.report_error("lte_modem", "connection", "Initial connection failed", "medium")

        except Exception as e:
            self.logger.error(f"Failed to initialize LTE controller: {e}")
            self.error_recovery.report_error("lte_modem", "initialization", str(e), "high")

    def _initialize_lte_monitor(self):
        """Initialize LTE network monitor"""
        try:
            if not self.lte_controller:
                self.logger.debug("LTE controller not initialized, skipping LTE monitor")
                return

            lte_config = self.config_manager.get_lte_config()

            monitor_config = LTEMonitorConfig(
                check_interval_secs=lte_config.get("check_interval_secs", 30.0),
                signal_weak_threshold_rssi=lte_config.get("signal_weak_threshold", 10),
                signal_critical_threshold_rssi=lte_config.get("signal_critical_threshold", 5),
                soft_recovery_threshold=lte_config.get("soft_recovery_threshold", 2),
                intermediate_recovery_threshold=lte_config.get("intermediate_recovery_threshold", 4),
                hard_recovery_threshold=lte_config.get("hard_recovery_threshold", 6),
                critical_escalation_threshold=lte_config.get("critical_escalation_threshold", 10),
            )

            self.lte_monitor = LTEMonitor(
                lte_controller=self.lte_controller,
                config=monitor_config,
                on_status=self._on_lte_status,
                on_disconnect=self._on_lte_disconnect,
                on_reconnect=self._on_lte_reconnect,
                error_recovery_system=self.error_recovery,
            )

            self.logger.info("LTE monitor initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize LTE monitor: {e}")

    def _is_lte_hardware_present(self) -> bool:
        """Check if LTE modem hardware is present via ModemManager"""
        try:
            import subprocess
            result = subprocess.run(
                ["mmcli", "-L"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and "SIM" in result.stdout.upper():
                self.logger.info("LTE modem detected via ModemManager")
                return True
            self.logger.info("No LTE modem detected")
            return False
        except Exception as e:
            self.logger.debug(f"LTE hardware detection failed: {e}")
            return False

    def _initialize_connectivity_manager(self):
        """Initialize connectivity manager for WiFi/LTE failover"""
        try:
            connectivity_config = self.config_manager.get_connectivity_config()

            # Determine mode from config
            mode_str = connectivity_config.get("mode", "lte_primary_wifi_backup")
            try:
                mode = ConnectivityMode(mode_str)
            except ValueError:
                self.logger.warning(f"Unknown connectivity mode '{mode_str}', using lte_primary_wifi_backup")
                mode = ConnectivityMode.LTE_PRIMARY_WIFI_BACKUP

            # Auto-detect: if LTE mode requested but no hardware, fall back to WiFi
            if mode in (ConnectivityMode.LTE_ONLY, ConnectivityMode.LTE_PRIMARY_WIFI_BACKUP):
                if not self._is_lte_hardware_present():
                    self.logger.warning("LTE mode configured but no LTE hardware detected - falling back to WiFi only")
                    mode = ConnectivityMode.WIFI_ONLY

            config = ConnectivityManagerConfig(
                mode=mode,
                failover_timeout_secs=connectivity_config.get("failover_timeout_secs", 60.0),
                failback_check_interval_secs=connectivity_config.get("failback_check_interval_secs", 300.0),
                failback_stability_secs=connectivity_config.get("failback_stability_secs", 30.0),
                status_report_interval_secs=connectivity_config.get("status_report_interval_secs", 900.0),
            )

            self.connectivity_manager = ConnectivityManager(
                config=config,
                wifi_monitor=self.network_monitor,
                lte_monitor=self.lte_monitor,
                error_recovery_system=self.error_recovery,
                on_connection_change=self._on_connectivity_change,
                on_status=self._on_connectivity_status,
                on_lte_wait_start=self._on_lte_wait_start,
                on_lte_wait_end=self._on_lte_wait_end,
            )

            # CRITICAL: Set WiFi intentionally disabled flag IMMEDIATELY if LTE is primary
            # This must happen BEFORE NetworkMonitor.start() is called to prevent
            # the monitor from attempting WiFi recovery while we want WiFi disabled
            if mode in (ConnectivityMode.LTE_PRIMARY_WIFI_BACKUP, ConnectivityMode.LTE_ONLY):
                if self.network_monitor and hasattr(self.network_monitor, 'set_wifi_intentionally_disabled'):
                    self.logger.info("LTE-first mode: marking WiFi as intentionally disabled before monitoring starts")
                    self.network_monitor.set_wifi_intentionally_disabled(True)

            self.logger.info(f"Connectivity manager initialized (mode: {mode.value})")

        except Exception as e:
            self.logger.error(f"Failed to initialize connectivity manager: {e}")

    def _on_lte_state_change(self, old_state, new_state):
        """Handle LTE modem state changes"""
        self.logger.info(f"LTE modem state changed: {old_state.value} -> {new_state.value}")

    def _on_lte_status(self, status: dict):
        """Handle LTE status updates"""
        rssi = status.get('signal_rssi', 99)
        quality = status.get('signal_quality', 'unknown')
        self.logger.debug(f"LTE status: RSSI={rssi}, quality={quality}")

    def _on_lte_disconnect(self, status: dict):
        """Handle LTE disconnect"""
        self.logger.warning(f"LTE disconnected: {status.get('error', 'unknown')}")

    def _on_lte_reconnect(self, status: dict):
        """Handle LTE reconnect"""
        self.logger.info(f"LTE reconnected: IP={status.get('ip_address', 'unknown')}")

    def _on_connectivity_change(self, old_type, new_type):
        """Handle connectivity type changes (WiFi/LTE failover)"""
        self.logger.info(f"Connectivity changed: {old_type.value} -> {new_type.value}")

    def _on_connectivity_status(self, status: dict):
        """Handle connectivity status updates"""
        active = status.get('active_connection', 'none')
        self.logger.debug(f"Connectivity status: active={active}")

    def _on_lte_wait_start(self, image_path: str, text: str):
        """Handle LTE startup wait beginning - show splash screen"""
        try:
            self.logger.info(f"LTE wait starting - showing splash: {text}")
            self.splash_screen = SplashScreen()
            self.splash_screen.show(
                text=text,
                image_path=image_path,
                text_position="bottom",
                font_size=32,
            )
        except Exception as e:
            self.logger.warning(f"Failed to show LTE splash screen: {e}")

    def _on_lte_wait_end(self, success: bool):
        """Handle LTE startup wait ending - hide splash screen"""
        try:
            if self.splash_screen:
                if success:
                    self.logger.info("LTE connected - hiding splash screen")
                else:
                    self.logger.warning("LTE failed to connect - hiding splash screen")
                self.splash_screen.hide()
                self.splash_screen = None
        except Exception as e:
            self.logger.warning(f"Failed to hide LTE splash screen: {e}")

    def _initialize_health_monitor(self):
        """Initialize system health monitoring"""
        try:
            health_thresholds = HealthThresholds(
                cpu_temp_warning_c=78.0,
                cpu_temp_critical_c=82.0,
                cpu_usage_warning_percent=80.0,
                memory_warning_percent=85.0,
                disk_warning_percent=85.0
            )
            
            self.health_monitor = HealthMonitor(
                thresholds=health_thresholds,
                check_interval=self.config_manager.monitoring_config.health_check_interval,
                on_health_update=self._on_health_update,
                on_alert=self._on_health_alert
            )
            
            self.logger.info("Health monitor initialized")
            self.error_recovery.report_success("system_health")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize health monitor: {e}")
            self.error_recovery.report_error("system_health", "initialization", str(e), "high")
    
    def _initialize_memory_optimizer(self):
        """Initialize memory optimizer for critical memory pressure management"""
        try:
            # PHASE 1 FIX: Use global singleton to prevent dual optimizer instances
            # Get the global singleton instance
            self.memory_optimizer = get_global_memory_optimizer()
            
            # Configure thresholds for Raspberry Pi with limited memory (Issue #39)
            memory_thresholds = MemoryThresholds(
                # More aggressive thresholds for 1GB Pi 4
                memory_warning_percent=70.0,     # Start optimization at 70%
                memory_critical_percent=80.0,    # Aggressive cleanup at 80% 
                memory_emergency_percent=90.0,   # Emergency at 90%
                # Swap thresholds based on issue analysis (243Mi/511Mi = 47%)
                swap_warning_percent=25.0,       # Warning at 25% swap usage
                swap_critical_percent=45.0,      # Critical at 45% swap usage  
                swap_emergency_percent=70.0,     # Emergency at 70% swap usage
                # Memory amounts optimized for Pi 4
                min_free_memory_mb=80.0,         # Keep 80MB free minimum
                gc_trigger_threshold_mb=120.0    # Trigger GC when below 120MB
            )
            
            # Configure the global singleton with production settings
            self.memory_optimizer.thresholds = memory_thresholds
            self.memory_optimizer.check_interval = 20.0  # Check every 20 seconds for critical monitoring
            self.memory_optimizer.enable_auto_optimization = True
            self.memory_optimizer.on_memory_alert = self._on_memory_alert
            
            # NOTE: Cleanup handlers are registered after components are initialized
            # (see _initialize_video_player and _register_memory_cleanup_handlers)
            
            # Start monitoring immediately for critical memory pressure
            # (Only start if not already running)
            if not self.memory_optimizer._running:
                self.memory_optimizer.start_monitoring()
            
            self.logger.info("Memory optimizer (global singleton) initialized and monitoring started")
            self.error_recovery.report_success("memory_optimizer")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize memory optimizer: {e}")
            self.error_recovery.report_error("memory_optimizer", "initialization", str(e), "critical")
    
    def _on_memory_alert(self, memory_status):
        """Handle memory alert from memory optimizer"""
        alert_msg = f"Memory Alert: {memory_status.alert_level.upper()} - "
        alert_msg += f"Memory: {memory_status.memory_percent:.1f}%, "
        alert_msg += f"Swap: {memory_status.swap_percent:.1f}%"
        
        if memory_status.alert_level == "emergency":
            self.logger.critical(alert_msg)
            # Report critical error to recovery system
            self.error_recovery.report_error(
                "memory_optimizer", 
                "emergency_memory_pressure", 
                alert_msg, 
                "critical"
            )
        elif memory_status.alert_level == "critical":
            self.logger.error(alert_msg)
            self.error_recovery.report_error(
                "memory_optimizer", 
                "critical_memory_pressure", 
                alert_msg, 
                "high"
            )
        elif memory_status.alert_level == "warning":
            self.logger.warning(alert_msg)
    
    def _initialize_tof_sensor(self):
        """Initialize ToF sensor and bin level monitor for recycling bin fill level tracking"""
        try:
            tof_config = self.config_manager.get_tof_config()

            if not tof_config.get("enabled", False):
                self.logger.info("ToF bin level sensor disabled in configuration")
                return

            if not TOF_SENSOR_AVAILABLE:
                self.logger.warning("ToF sensor module not available - skipping")
                return

            self.logger.info("Initializing ToF bin level sensor...")

            sensor_config = ToFSensorConfig(
                i2c_address=tof_config.get("i2c_address", 0x29),
                timing_budget_us=tof_config.get("timing_budget_us", 200_000),
                sample_count=tof_config.get("sample_count", 7),
                empty_distance_mm=tof_config.get("empty_distance_mm", 800),
                full_distance_mm=tof_config.get("full_distance_mm", 205),
                simulation_mode=tof_config.get("simulation_mode", False),
            )

            self.tof_sensor = ToFSensor(config=sensor_config)

            if not self.tof_sensor.connect():
                self.logger.warning("ToF sensor failed to connect")
                self.error_recovery.report_error(
                    "tof_sensor", "connection", "Initial connection failed", "medium"
                )
                return

            monitor_config = BinLevelMonitorConfig(
                check_interval_secs=tof_config.get("check_interval_secs", 1800.0),
                empty_distance_mm=tof_config.get("empty_distance_mm", 800),
                full_distance_mm=tof_config.get("full_distance_mm", 205),
            )

            self.bin_level_monitor = BinLevelMonitor(
                tof_sensor=self.tof_sensor,
                config=monitor_config,
                on_level_update=self._on_bin_level_update,
                error_recovery_system=self.error_recovery,
            )

            self.logger.info("ToF sensor and bin level monitor initialized")
            self.error_recovery.report_success("tof_sensor")

        except Exception as e:
            self.logger.error(f"Failed to initialize ToF sensor: {e}")
            self.error_recovery.report_error("tof_sensor", "initialization", str(e), "medium")

    def _on_bin_level_update(self, fill_data: dict):
        """Handle bin fill level updates from the monitor"""
        self.logger.info(
            f"Bin level update: {fill_data['fill_level']} "
            f"({fill_data['fill_percentage']}%) at {fill_data['distance_mm']}mm"
        )

    def _initialize_aws_manager(self):
        """Initialize AWS IoT manager with resilience and unique client ID to prevent DUPLICATE_CLIENTID errors"""
        try:
            # OPTIMIZED: Faster reconnection for production IoT (Issue #TS_538A7DD4)
            # - Initial delay: 2.0s → 1.0s (faster first retry)
            # - Max delay: 300s → 120s (more aggressive reconnection)
            # Backoff sequence: 1s → 1.5s → 2.25s → 3.4s → 5.1s → ... → 120s (cap)
            retry_config = RetryConfig(
                initial_delay=1.0,   # OPTIMIZED: Start faster
                max_delay=120.0,     # OPTIMIZED: Cap at 2 min (was 5 min)
                multiplier=1.5,
                jitter=0.2
            )
            
            self.aws_manager = ResilientAWSManager(
                thing_name=self.aws_config["thing_name"],
                endpoint=self.aws_config["endpoint"],
                cert_path=str(self.aws_config["cert_path"]),
                key_path=str(self.aws_config["key_path"]),
                ca_path=str(self.aws_config["ca_path"]),
                retry_config=retry_config,
                use_unique_client_id=True  # Enable unique client IDs to prevent DUPLICATE_CLIENTID errors
            )
            
            # Set callbacks
            self.aws_manager.set_callbacks(
                on_success=self._on_aws_connected,
                on_lost=self._on_aws_disconnected
            )
            
            self.aws_manager.set_image_display_callback(self._on_product_image_display)
            self.aws_manager.set_no_match_display_callback(self._on_no_match_display)
            self.aws_manager.set_qr_code_display_callback(self._on_cloud_qr_code_display)
            
            self.logger.info("AWS manager initialized")
            self.error_recovery.report_success("aws_connection")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize AWS manager: {e}")
            self.error_recovery.report_error("aws_connection", "initialization", str(e), "high")
    
    def _initialize_ota_manager(self):
        """Initialize OTA (Over-The-Air) update manager"""
        try:
            if not self.aws_manager:
                self.logger.warning("AWS manager not available - OTA disabled")
                return
            
            # Check if OTA is enabled in config
            if not self.config_manager.get_ota_config().get("ENABLED", True):
                self.logger.info("OTA updates disabled in configuration")
                return
            
            self.ota_manager = OTAManager(
                aws_manager=self.aws_manager,
                config=self.config_manager.get_full_config(),
                logger=self.logger
            )
            
            # Register OTA manager with AWS manager
            self.aws_manager.set_ota_manager(self.ota_manager)
            
            # Initialize OTA capabilities
            if self.aws_manager.connected:
                success = self.aws_manager.initialize_ota_capabilities()
                if success:
                    self.logger.info("✅ OTA manager initialized and ready")
                    self.error_recovery.report_success("ota_manager")
                else:
                    self.logger.warning("⚠️  OTA manager initialized but Jobs client setup failed")
            else:
                self.logger.info("ℹ️  OTA manager initialized - will connect when AWS IoT is ready")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize OTA manager: {e}")
            self.error_recovery.report_error("ota_manager", "initialization", str(e), "medium")
    
    def _initialize_servo_controller(self):
        """Initialize STServo controller if available"""
        try:
            if SERVO_AVAILABLE:
                # Initialize STServo controller (uses USB serial, not GPIO)
                # Configuration loaded from environment variables or defaults
                self.servo_controller = STServoController()
                if not getattr(self.servo_controller, "simulation_mode", False) and not getattr(
                    self.servo_controller, "is_connected", False
                ):
                    self.logger.error(
                        "STServo controller initialized without a hardware connection; "
                        "door commands will fail until the USB servo adapter is reconnected"
                    )
                    self.error_recovery.report_error(
                        "servo_controller",
                        "initialization",
                        "servo adapter not connected",
                        "medium",
                    )
                else:
                    self.logger.info("STServo controller initialized")

                # Ensure servo is at closed position on startup
                try:
                    self.logger.info("Initializing servo to closed position...")
                    if self.servo_controller.close_door(hold_time=0.5):
                        self.logger.info("Servo initialized to closed position")
                        self.error_recovery.report_success("servo_controller")
                    else:
                        self.logger.error("Failed to initialize servo to closed position")
                        self.error_recovery.report_error(
                            "servo_controller",
                            "initialization",
                            "failed to close servo at startup",
                            "medium",
                        )
                except Exception as servo_init_error:
                    self.logger.warning(f"Failed to initialize servo to closed position: {servo_init_error}")
            else:
                self.logger.warning("STServo controller not available")

        except Exception as e:
            self.logger.error(f"Failed to initialize servo controller: {e}")
            self.error_recovery.report_error("servo_controller", "initialization", str(e), "medium")

    def _initialize_recycle_sensor(self):
        """Initialize ToF sensor for recycling verification"""
        try:
            if RECYCLE_SENSOR_AVAILABLE:
                sensor_config = RecycleSensorConfig(
                    simulation_mode=os.environ.get(
                        'TSV6_RECYCLE_SENSOR_SIMULATION', 'false'
                    ).lower() in ('true', '1', 'yes')
                )
                self.recycle_sensor = RecycleSensor(config=sensor_config)
                self.logger.info("Recycle verification sensor initialized")
                self.error_recovery.report_success("recycle_sensor")
            else:
                self.logger.warning(
                    "Recycle sensor not available - items will be marked as recycled "
                    "without physical verification"
                )
        except Exception as e:
            self.logger.error(f"Failed to initialize recycle sensor: {e}")
            self.error_recovery.report_error("recycle_sensor", "initialization", str(e), "low")

    def _initialize_pisignage(self):
        """
        Initialize the display backend based on the PISIGNAGE_BACKEND env var.

        PISIGNAGE_BACKEND values:
          "rest"   — PiSignageAdapter (REST client to remote player; default)
          "native" — TSV6NativeBackend (in-process player; no player license needed)
          "vlc"    — Skip this init; legacy EnhancedVideoPlayer is used
          (unset)  — Treated as "rest" when PISIGNAGE_ENABLED=true, else "vlc"

        The selected backend is stored as ``self.display_backend`` (a
        DisplayController) and ``self._pisignage_enabled`` is set True so that
        all display callsites route through the backend instead of video_player.
        """
        from tsv6.config.config import config

        backend_type = os.environ.get("PISIGNAGE_BACKEND", "rest").lower().strip()

        # If the legacy override is explicitly requested, skip all display backends.
        if backend_type == "vlc":
            self.logger.info("PISIGNAGE_BACKEND=vlc — using legacy EnhancedVideoPlayer")
            return

        # Both "rest" and "native" require PISIGNAGE_ENABLED=true OR an explicit
        # PISIGNAGE_BACKEND value to be meaningful.  If neither signal is present,
        # stay on VLC.
        if not config.pisignage.enabled and backend_type == "rest":
            self.logger.info("PiSignage disabled (PISIGNAGE_ENABLED != true)")
            return

        self.error_recovery.register_component("pisignage")

        # ── Native backend ────────────────────────────────────────────────────
        if backend_type == "native":
            if not TSV6_NATIVE_AVAILABLE:
                self.logger.warning(
                    "TSV6NativeBackend not importable (PISIGNAGE_BACKEND=native) "
                    "— falling back to VLC"
                )
                return

            try:
                from pathlib import Path

                server_url = os.environ.get(
                    "PISIGNAGE_SERVER_URL", "http://72.60.120.25:3000"
                )
                username = os.environ.get("PISIGNAGE_USERNAME", "pi")
                password = os.environ.get("PISIGNAGE_PASSWORD", "pi")
                installation = os.environ.get("PISIGNAGE_INSTALLATION", "g1tech26")
                group_name = os.environ.get("PISIGNAGE_GROUP", "default")
                app_version = os.environ.get("TSV6_APP_VERSION", "1.0.0")
                venue_id = os.environ.get("TSV6_VENUE_ID") or None

                # Resolve layout HTML — defaults to the bundled router_page.html
                # (the standalone signage_main entry point uses the same file).
                # The legacy pisignage/templates/layouts/custom_layout.html had a
                # hard-coded ::after footer that suppresses any dynamic ticker
                # injection.
                default_layout = (
                    Path(__file__).parent.parent
                    / "display"
                    / "tsv6_player"
                    / "router_page.html"
                )
                layout_html = Path(
                    os.environ.get("TSV6_LAYOUT_HTML", str(default_layout))
                )

                cache_dir = Path(
                    os.environ.get(
                        "TSV6_ASSET_CACHE_DIR",
                        str(Path.home() / ".local" / "share" / "tsv6" / "assets"),
                    )
                )

                backend = TSV6NativeBackend(
                    server_url=server_url,
                    username=username,
                    password=password,
                    cache_dir=cache_dir,
                    layout_html=layout_html,
                    installation=installation,
                    group_name=group_name,
                    app_version=app_version,
                    venue_id=venue_id,
                )

                if not backend.connect():
                    self.logger.error(
                        "TSV6NativeBackend.connect() failed — falling back to VLC"
                    )
                    return

                backend.start()

                self.display_backend = backend
                self._pisignage_enabled = True
                self._wire_settings_wake_callback()
                self._wire_settings_motor_callback()
                self.error_recovery.report_success("pisignage")
                self.logger.info(
                    "TSV6NativeBackend initialized — server=%s installation=%s",
                    server_url,
                    installation,
                )

            except Exception as exc:
                self.logger.error("TSV6NativeBackend initialization failed: %s", exc)
                self.display_backend = None
                self._pisignage_enabled = False
            return

        # ── REST backend (default) ─────────────────────────────────────────────
        if not PISIGNAGE_AVAILABLE:
            self.logger.warning("PiSignage modules not importable — falling back to VLC")
            return

        try:
            # PiSignageConfig reads credentials from env vars directly
            self.pisignage_adapter = PiSignageAdapter(
                config=PiSignageConfig(),
                on_connection_change=self._on_pisignage_connection_change,
            )

            # Verify server is reachable
            if not self.pisignage_adapter.health_check():
                self.logger.error(
                    "PiSignage server unreachable at %s — falling back to VLC",
                    self.pisignage_adapter.server_url,
                )
                self.pisignage_adapter = None
                return

            # Discover the player
            if not self.pisignage_adapter.connect():
                self.logger.warning(
                    "No PiSignage player registered — falling back to VLC"
                )
                self.pisignage_adapter = None
                return

            # Ensure playlists exist on the server
            playlist_mgr = PlaylistManager(self.pisignage_adapter)
            playlist_mgr.ensure_playlists_exist()

            # Start health monitor
            self.pisignage_health_monitor = PiSignageHealthMonitor(
                adapter=self.pisignage_adapter,
                check_interval=self.pisignage_adapter._config.health_check_interval,
                on_server_down=self._on_pisignage_down,
                on_server_recovered=self._on_pisignage_recovered,
            )
            self.pisignage_health_monitor.start()

            # Wire the adapter as the unified display backend.
            self.display_backend = self.pisignage_adapter
            self._pisignage_enabled = True
            self.error_recovery.report_success("pisignage")
            self.logger.info(
                "PiSignage REST adapter initialized — server=%s player=%s",
                self.pisignage_adapter.server_url,
                self.pisignage_adapter.player_id,
            )

        except Exception as exc:
            self.logger.error("PiSignage initialization failed: %s", exc)
            self.pisignage_adapter = None
            self.display_backend = None
            self._pisignage_enabled = False

    def _on_pisignage_connection_change(self, connected: bool):
        """Handle PiSignage connection state changes."""
        if connected:
            self.logger.info("PiSignage server connection restored")
            self.error_recovery.report_success("pisignage")
        else:
            self.logger.warning("PiSignage server connection lost")
            self.error_recovery.report_error(
                "pisignage", "connection_lost", "Server unreachable", "high"
            )

    def _on_pisignage_down(self):
        """PiSignage server has been unreachable for multiple health checks."""
        self.logger.error("PiSignage server DOWN — display may be stale")
        # Could trigger VLC fallback here if desired

    def _on_pisignage_recovered(self):
        """PiSignage server recovered after being down."""
        self.logger.info("PiSignage server recovered — resuming normal operation")
        if self.display_backend is not None:
            self.display_backend.show_idle()
        elif self.pisignage_adapter:
            self.pisignage_adapter.set_default_playlist()

    def _initialize_video_player(self):
        """Initialize video player component (skipped when PiSignage is active)"""
        if self._pisignage_enabled:
            self.logger.info(
                "VLC video player skipped — PiSignage is handling display"
            )
            return

        try:
            # PHASE 1 FIX: Pass memory optimizer to video player to ensure same instance
            self.video_player = EnhancedVideoPlayer(
                aws_manager=self.aws_manager,
                memory_optimizer=self.memory_optimizer  # Pass the global singleton
            )
            
            # Override cleanup to integrate with our system
            original_cleanup = self.video_player.cleanup_and_exit
            self.video_player.cleanup_and_exit = self._handle_video_player_exit
            
            # PHASE 3: Register VLC cleanup handler with memory optimizer
            if self.memory_optimizer and hasattr(self.video_player, 'cleanup_resources'):
                self.memory_optimizer.register_cleanup_handler(self.video_player.cleanup_resources)
                self.logger.info("VLC cleanup handler registered with memory optimizer")
            
            self.logger.info("Video player initialized with shared memory optimizer")
            self.error_recovery.report_success("video_player")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize video player: {e}")
            self.error_recovery.report_error("video_player", "initialization", str(e), "critical")
    
    def _initialize_barcode_scanner(self):
        """Initialize barcode scanner with error handling"""
        try:
            self.barcode_scanner = OptimizedBarcodeScanner(aws_manager=self.aws_manager)
            self.barcode_scanner.barcode_callback = self._on_barcode_scanned
            self.barcode_scanner.qr_code_callback = self._display_qr_not_allowed_image
            
            self.logger.info("Barcode scanner initialized")
            self.error_recovery.report_success("barcode_scanner")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize barcode scanner: {e}")
            self.error_recovery.report_error("barcode_scanner", "initialization", str(e), "high")
    
    def _register_enhanced_recovery_handlers(self):
        """Register enhanced recovery handlers with escalation support"""
        
        def network_recovery_handler(action: RecoveryAction, error, escalation_level: EscalationLevel):
            # Network recovery is handled by NetworkManager (Layer 0) and the
            # shell watchdog (Layer 2).  The Python NetworkMonitor is observe-only.
            # This handler only restarts the monitor thread to reset its state.
            self.logger.info(f"Network recovery requested ({escalation_level.value}): {action} — deferring to NetworkManager")
            try:
                if action in (RecoveryAction.RESET_CONNECTION, RecoveryAction.RELOAD_WIFI_DRIVER,
                              RecoveryAction.RESTART_SERVICE):
                    # Restart the observe-only monitor to reset failure counters
                    if self.network_monitor:
                        self.network_monitor.stop()
                        time.sleep(3)
                        self.network_monitor.start()
                    return True
            except Exception as e:
                self.logger.error(f"Network monitor restart failed: {e}")
            return False
        
        def aws_recovery_handler(action: RecoveryAction, error, escalation_level: EscalationLevel):
            self.logger.info(f"Attempting {escalation_level.value} AWS recovery: {action}")
            try:
                if action == RecoveryAction.RESET_CONNECTION:
                    # Soft recovery - reset AWS connection
                    if self.aws_manager:
                        self.aws_manager.stop_auto_reconnect()
                        time.sleep(5)
                        self.aws_manager.start_auto_reconnect()
                        return True
                        
                elif action == RecoveryAction.RESTART_SERVICE:
                    # Intermediate recovery - full AWS manager restart
                    if self.aws_manager:
                        self.aws_manager.disconnect()
                        time.sleep(10)
                        self.aws_manager.connect()
                        self.aws_manager.start_auto_reconnect()
                        return True
                        
                elif action == RecoveryAction.FALLBACK_MODE:
                    # Hard recovery - reinitialize AWS manager
                    try:
                        self._initialize_aws_manager()
                        if self.aws_manager:
                            self.aws_manager.connect()
                            self.aws_manager.start_auto_reconnect()
                        return True
                    except:
                        pass
                        
            except Exception as e:
                self.logger.error(f"AWS recovery failed: {e}")
            return False
        
        def video_player_recovery_handler(action: RecoveryAction, error, escalation_level: EscalationLevel):
            self.logger.info(f"Attempting {escalation_level.value} video player recovery: {action}")
            try:
                if action == RecoveryAction.RESTART_COMPONENT:
                    # Restart current video
                    if self.video_player and hasattr(self.video_player, 'play_current_video'):
                        self.video_player.play_current_video()
                        return True
                        
                elif action == RecoveryAction.RESTART_SERVICE:
                    # Reinitialize video player
                    if self.video_player:
                        self.video_player.cleanup()
                    self._initialize_video_player()
                    return True
                    
                elif action == RecoveryAction.FALLBACK_MODE:
                    # Switch to safe mode video playback
                    if self.video_player and hasattr(self.video_player, 'safe_mode'):
                        self.video_player.safe_mode()
                        return True
                        
            except Exception as e:
                self.logger.error(f"Video player recovery failed: {e}")
            return False
        
        def barcode_scanner_recovery_handler(action: RecoveryAction, error, escalation_level: EscalationLevel):
            self.logger.info(f"Attempting {escalation_level.value} barcode scanner recovery: {action}")
            try:
                if action == RecoveryAction.RESTART_COMPONENT:
                    # Restart scanning
                    if self.barcode_scanner:
                        self.barcode_scanner.stop_scanning()
                        time.sleep(2)
                        self.barcode_scanner.start_scanning()
                        return True
                        
                elif action == RecoveryAction.RESTART_SERVICE:
                    # Reinitialize scanner
                    if self.barcode_scanner:
                        self.barcode_scanner.stop_scanning()
                    self._initialize_barcode_scanner()
                    if self.barcode_scanner:
                        self.barcode_scanner.start_scanning()
                    return True
                    
                elif action == RecoveryAction.FALLBACK_MODE:
                    # Disable barcode scanning to prevent further errors
                    if self.barcode_scanner:
                        self.barcode_scanner.stop_scanning()
                        self.logger.warning("Barcode scanner disabled in fallback mode")
                    return True
                    
            except Exception as e:
                self.logger.error(f"Barcode scanner recovery failed: {e}")
            return False
        
        def servo_controller_recovery_handler(action: RecoveryAction, error, escalation_level: EscalationLevel):
            self.logger.info(f"Attempting {escalation_level.value} servo controller recovery: {action}")
            try:
                if action == RecoveryAction.RESTART_COMPONENT:
                    # Reset servo to neutral position
                    if self.servo_controller:
                        self.servo_controller.close_door()
                        return True
                        
                elif action == RecoveryAction.RESTART_SERVICE:
                    # Reinitialize servo controller
                    if self.servo_controller:
                        self.servo_controller.cleanup()
                    self._initialize_servo_controller()
                    return True
                    
                elif action == RecoveryAction.FALLBACK_MODE:
                    # Disable servo to prevent further errors
                    if self.servo_controller:
                        self.servo_controller.cleanup()
                        self.servo_controller = None
                        self.logger.warning("Servo controller disabled in fallback mode")
                    return True
                    
            except Exception as e:
                self.logger.error(f"Servo controller recovery failed: {e}")
            return False
        
        def memory_optimizer_recovery_handler(action: RecoveryAction, error, escalation_level: EscalationLevel):
            """PHASE 2: Memory optimizer recovery handler"""
            self.logger.info(f"Attempting {escalation_level.value} memory optimizer recovery: {action}")
            try:
                if action == RecoveryAction.RESTART_COMPONENT:
                    # Force immediate memory optimization
                    if self.memory_optimizer:
                        self.logger.info("Forcing aggressive memory optimization")
                        self.memory_optimizer.optimize_memory_usage(force=True)
                        return True
                        
                elif action == RecoveryAction.RESTART_SERVICE:
                    # Stop and restart monitoring with more aggressive settings
                    if self.memory_optimizer:
                        self.logger.info("Restarting memory optimizer with aggressive thresholds")
                        self.memory_optimizer.stop_monitoring()
                        time.sleep(2)
                        
                        # Tighten thresholds for recovery
                        self.memory_optimizer.thresholds.memory_warning_percent = 65.0
                        self.memory_optimizer.thresholds.memory_critical_percent = 75.0
                        self.memory_optimizer.thresholds.memory_emergency_percent = 85.0
                        
                        self.memory_optimizer.start_monitoring()
                        return True
                        
                elif action == RecoveryAction.FALLBACK_MODE:
                    # Emergency memory cleanup and VLC restart
                    self.logger.warning("Emergency memory recovery - restarting video player")
                    if self.video_player:
                        # Stop video playback to free memory
                        if hasattr(self.video_player, 'player') and self.video_player.player:
                            self.video_player.player.stop()
                        
                        # Clear media cache
                        if hasattr(self.video_player, 'media_cache'):
                            self.video_player.media_cache.clear()
                        
                        # Force aggressive GC
                        import gc
                        gc.collect()
                        gc.collect()  # Double collection
                        
                        # Restart video after cleanup
                        time.sleep(2)
                        if hasattr(self.video_player, 'play_current_video'):
                            self.video_player.play_current_video()
                    
                    return True
                    
            except Exception as e:
                self.logger.error(f"Memory optimizer recovery failed: {e}")
            return False
        
        # Register enhanced handlers
        self.error_recovery.register_recovery_handler("network", network_recovery_handler)
        self.error_recovery.register_recovery_handler("aws_connection", aws_recovery_handler)
        self.error_recovery.register_recovery_handler("video_player", video_player_recovery_handler)
        self.error_recovery.register_recovery_handler("barcode_scanner", barcode_scanner_recovery_handler)
        self.error_recovery.register_recovery_handler("servo_controller", servo_controller_recovery_handler)
        self.error_recovery.register_recovery_handler("memory_optimizer", memory_optimizer_recovery_handler)
        
        # Register fallback handlers
        def system_fallback_handler(error):
            self.logger.critical(f"System fallback triggered: {error.error_message}")
            # Implement safe mode operations
            try:
                # Stop all non-critical components
                if self.barcode_scanner:
                    self.barcode_scanner.stop_scanning()
                if self.servo_controller:
                    self.servo_controller.cleanup()
                    self.servo_controller = None
                
                # Keep only essential services running
                self.logger.warning("System in fallback mode - non-critical components disabled")
            except Exception as e:
                self.logger.error(f"Fallback handler error: {e}")
        
        self.error_recovery.register_fallback_handler("system", system_fallback_handler)
        
        self.logger.info("Enhanced recovery handlers registered")
    
    def _on_network_status(self, status):
        """Handle network status updates from observe-only NetworkMonitor"""
        if "warning" in status:
            self.logger.warning(f"Network warning: {status}")

    def _on_network_disconnect(self, status):
        """Handle network disconnection — recovery is handled by NetworkManager (Layer 0)"""
        self.logger.error(f"Network disconnected: {status}")
        try:
            if self.network_deadline_monitor:
                self.network_deadline_monitor.mark_disconnected()
        except Exception as e:
            self.logger.error(f"Error updating network deadline monitor on disconnect: {e}")

    def _on_network_reconnect(self, status):
        """Handle network reconnection (detected by observe-only monitor)"""
        self.logger.info(f"Network reconnected: {status}")
        try:
            if self.network_deadline_monitor:
                self.network_deadline_monitor.mark_connected()
        except Exception as e:
            self.logger.error(f"Error updating network deadline monitor on reconnect: {e}")
        # Trigger AWS reconnection if needed
        if self.aws_manager and not self.aws_manager.connected:
            self.aws_manager.start_auto_reconnect()
        self._restart_vengo_after_network_reconnect()

    def _restart_vengo_after_network_reconnect(self):
        """Re-issue the idle/Vengo display command after Wi-Fi comes back."""
        if self.display_backend is None:
            return

        now = time.monotonic()
        with self._vengo_reconnect_lock:
            if now - self._last_vengo_reconnect_restart_at < 15.0:
                self.logger.debug("Skipping Vengo reconnect restart; recently attempted")
                return
            self._last_vengo_reconnect_restart_at = now

        delay = float(os.environ.get("TSV6_VENGO_RECONNECT_RESTART_DELAY_SECS", "2.0"))

        def restart_idle():
            try:
                if delay > 0:
                    time.sleep(delay)

                metrics = {}
                if hasattr(self.display_backend, "get_metrics"):
                    metrics = self.display_backend.get_metrics() or {}
                state = metrics.get("renderer_state") or metrics.get("state") or ""
                if state and state not in ("idle", "vengo_idle", "offline", "stopped", "uninitialised"):
                    self.logger.info(
                        "Network reconnected; leaving display state %r untouched",
                        state,
                    )
                    return

                self.logger.info("Network reconnected; restarting Vengo idle player")
                if not self.display_backend.show_idle():
                    self.logger.warning("Vengo idle restart after network reconnect returned false")
            except Exception as e:
                self.logger.error(f"Failed to restart Vengo idle after network reconnect: {e}")

        threading.Thread(
            target=restart_idle,
            name="VengoReconnectRestart",
            daemon=True,
        ).start()
    
    def _on_health_update(self, metrics):
        """Handle health metric updates"""
        # Report health issues to error recovery
        if metrics.overall_health == "critical":
            for alert in metrics.alerts:
                self.error_recovery.report_error("system_health", "critical_metric", alert, "critical")
        elif metrics.overall_health == "warning":
            for alert in metrics.alerts:
                self.error_recovery.report_error("system_health", "warning_metric", alert, "medium")
        else:
            self.error_recovery.report_success("system_health")
    
    def _on_health_alert(self, severity, alerts):
        """Handle health alerts"""
        self.logger.warning(f"Health alert ({severity}): {', '.join(alerts)}")
    
    def _on_aws_connected(self):
        """Handle AWS connection success"""
        self.logger.info("AWS IoT connected")
        self.error_recovery.report_success("aws_connection")
        
        # CRITICAL FIX: Add error handling around connection tracker operations
        try:
            self.connection_tracker.mark_connected()
        except Exception as e:
            self.logger.error(f"Error updating connection tracker on connect: {e}")
        
        try:
            self.connection_deadline_monitor.mark_connected()
        except Exception as e:
            self.logger.error(f"Error updating deadline monitor on connect: {e}")
        
        # Log connection metrics (non-critical, don't fail if this errors)
        try:
            metrics = self.connection_tracker.get_status_summary()
            self.logger.info(
                f"Connection metrics: uptime={metrics['current_uptime_minutes']:.1f} min, "
                f"24h uptime={metrics['uptime_percentage_24h']:.1f}%, "
                f"reconnections={metrics['successful_reconnections']}"
            )
        except Exception as e:
            self.logger.warning(f"Could not retrieve connection metrics: {e}")
    
    def _on_aws_disconnected(self, error):
        """Handle AWS disconnection"""
        self.logger.error(f"AWS IoT disconnected: {error}")
        self.error_recovery.report_error("aws_connection", "disconnected", str(error), "high")
        
        # CRITICAL FIX: Add error handling around connection tracker operations
        # These must not fail or they'll prevent deadline monitoring from starting
        try:
            self.connection_tracker.mark_disconnected()
        except Exception as e:
            self.logger.error(f"Error updating connection tracker on disconnect: {e}")
        
        try:
            self.connection_deadline_monitor.mark_disconnected()
        except Exception as e:
            self.logger.error(f"Error updating deadline monitor on disconnect: {e}")
        
        # Log connection metrics (non-critical, don't fail if this errors)
        try:
            metrics = self.connection_tracker.get_status_summary()
            self.logger.warning(
                f"Connection lost - metrics: downtime={metrics['current_downtime_minutes']:.1f} min, "
                f"attempts={metrics['reconnection_attempts']}, "
                f"24h uptime={metrics['uptime_percentage_24h']:.1f}%"
            )
        except Exception as e:
            self.logger.warning(f"Could not retrieve connection metrics: {e}")
    
    def _on_product_image_display(self, product_data):
        """
        Handle product image display requests from AWS openDoor response.

        Two-step verification flow:
        1. Show "Please Deposit Your Item" screen (instead of product+QR immediately)
        2. Open door, monitor IR sensor, close door
        3. If item detected: show product image + QR + NFC
        4. If not detected: show "Item Not Detected" error
        """
        try:
            # Guard: prevent concurrent door sequences (causes open/close loop)
            with self._door_sequence_lock:
                if self._door_sequence_active:
                    self.logger.warning(
                        f"Door sequence already active — ignoring openDoor for "
                        f"{product_data.get('barcode', '?')}"
                    )
                    return
                self._door_sequence_active = True

            # Cache the cloud-supplied no-item playlist override BEFORE launching
            # the door thread so the recycle_sensor timeout path can pick it up.
            # V1 payloads omit this field, so the value falls back to None and
            # the display backend renders its default tsv6_no_item playlist.
            self._pending_no_item_playlist = (
                product_data.get("noItemPlaylist") if isinstance(product_data, dict) else None
            )

            # The deposit-item playlist is already showing from _on_barcode_scanned
            # (it loops through the entire transaction). Legacy VLC fallback
            # still needs the explicit deposit-waiting trigger here.
            if self.display_backend is None and self.video_player and hasattr(
                self.video_player, 'display_deposit_waiting'
            ):
                self.video_player.display_deposit_waiting()

            # Run verified door sequence in background to avoid blocking MQTT
            if self.servo_controller or self.recycle_sensor:
                door_thread = threading.Thread(
                    target=self._verified_door_sequence,
                    args=(product_data,),
                    daemon=True
                )
                door_thread.start()
            else:
                # No servo and no sensor — fallback to immediate display
                self.logger.warning("No servo or sensor — falling back to immediate product display")
                product_image_path = product_data.get('imageUrl', '')
                qr_url = product_data.get('qrUrl', product_data.get('nfcUrl', ''))
                nfc_url = product_data.get('nfcUrl', None)
                if self.display_backend is not None:
                    self.display_backend.show_product_display(
                        product_image_path=product_image_path,
                        qr_url=qr_url,
                        nfc_url=nfc_url,
                    )
                else:
                    if self.video_player and hasattr(self.video_player, 'hide_deposit_waiting'):
                        self.video_player.hide_deposit_waiting()
                    if self.video_player and hasattr(self.video_player, 'display_product_image'):
                        self.video_player.display_product_image(product_data)
                with self._door_sequence_lock:
                    self._door_sequence_active = False

        except Exception as e:
            with self._door_sequence_lock:
                self._door_sequence_active = False
            self.logger.error(f"Failed to handle product display: {e}")
            self.error_recovery.report_error("servo_controller", "door_open_failed", str(e), "medium")

    def _verified_door_sequence(self, product_data):
        """
        Execute door open/close with IR sensor verification.

        Sequence:
        1. Start door opening in background thread
        2. After ~1 second (door ~50% open), start IR monitoring early
        3. Wait for door to finish opening
        4. Wait for IR detection OR 3-second timeout (after door fully open)
        5. Stop IR monitoring before door closes
        6. Close door with safety monitoring
        7. Handle result (success → product+QR, failure → error message)
        """
        try:
            product_name = product_data.get('productName', 'Unknown')
            barcode = product_data.get('barcode', '')
            transaction_id = product_data.get('transactionId', '')
            nfc_url = product_data.get('nfcUrl', '')

            print(f"Opening door for: {product_name}")

            # 1. Start ToF transaction BEFORE opening door
            #    Two-detection verification:
            #    - Detection 1: Door swings past sensor (~0.7s)
            #    - Detection 2: Item falls through chute
            #    Both required = item recycled. Only #1 = no item deposited.
            item_detected = False
            if self.recycle_sensor:
                self.recycle_sensor.start_monitoring()
                print("   ToF sensor transaction started — expecting door + item...")

            # 2. Open door (detection #1 happens as door swings past sensor)
            door_open_thread = None
            if self.servo_controller:
                print("   Opening door...")
                door_open_thread = threading.Thread(
                    target=self.servo_controller.open_door,
                    kwargs={'hold_time': 0},
                    daemon=True
                )
                door_open_thread.start()

            # 3. Wait for both detections (door + item) or timeout
            if self.recycle_sensor and self._recycle_verification_required:
                if door_open_thread:
                    door_open_thread.join(timeout=5.0)

                # Wait for detection #2 (item) — #1 (door) happens during open
                item_detected = self.recycle_sensor.detection_event.wait(timeout=5.0)

                # 4. End transaction
                self.recycle_sensor.stop_monitoring()

                count = self.recycle_sensor.get_detection_count()
                if item_detected:
                    print(f"   Item verified! ({count} detections: door + item)")
                    # Safety delay — let user pull hand out before door closes
                    time.sleep(1.0)
                else:
                    print(f"   No item detected ({count} detection(s) — door only)")
            else:
                # Verification disabled (TSV6_RECYCLE_VERIFICATION_REQUIRED=false)
                # or no sensor available — treat the deposit as successful and
                # hold the door open for the standard 3s window.
                if self.recycle_sensor and not self._recycle_verification_required:
                    self.logger.info(
                        "Recycle verification disabled by config — "
                        "auto-success after door hold"
                    )
                    self.recycle_sensor.stop_monitoring()
                else:
                    self.logger.warning("No recycle sensor — skipping verification")
                item_detected = True
                if door_open_thread:
                    door_open_thread.join(timeout=5.0)
                time.sleep(3.0)  # Hold door open for 3 seconds like original behavior

            # 6. Close door with obstruction detection and retry
            if self.servo_controller:
                print("   Closing door with safety monitoring...")
                success, status = self.servo_controller.close_door_with_safety(
                    max_retries=3,
                    retry_delay=5.0,
                    hold_time=0.5
                )

                if success:
                    self.error_recovery.report_success("servo_controller")
                elif status == "obstructed":
                    if self.recycle_sensor:
                        self.recycle_sensor.stop_monitoring()
                    self._handle_obstruction_detected()
                    return
                else:
                    self.logger.error(f"Door close failed with status: {status}")
                    self.error_recovery.report_error("servo_controller", "door_close_failed", status, "medium")

            # 6. Handle verification result
            if item_detected:
                self._handle_recycle_success(product_data, nfc_url, transaction_id)
            else:
                self._handle_recycle_failure(product_data, barcode, transaction_id)

        except Exception as e:
            if self.recycle_sensor:
                self.recycle_sensor.stop_monitoring()
            self.logger.error(f"Verified door sequence failed: {e}")
            self.error_recovery.report_error("servo_controller", "door_sequence_failed", str(e), "medium")

        finally:
            # Always release the door sequence lock so next scan can proceed
            with self._door_sequence_lock:
                self._door_sequence_active = False

    def _handle_recycle_success(self, product_data, nfc_url: str, transaction_id: str):
        """
        Handle successful recycling verification.

        Called AFTER door is closed and item was detected by sensor.
        Shows product image + QR code, publishes success, starts NFC.
        """
        barcode = product_data.get('barcode', '')
        self.logger.info(f"Recycle SUCCESS for barcode: {barcode}")
        print(f"Recycle verified successfully")

        # Show product image + QR code on whichever display backend is active.
        # V2 cloud returns the displayable URL in `productImage` (WebP if the
        # cold-UPC path completed conversion, source-URL fallback otherwise,
        # or null on the very first scan before WebP is built). V1 used
        # `imageUrl`. Prefer the V2 field, fall back to the V1 field, then to
        # the original-source URL.
        product_image_path = (
            product_data.get('productImage')
            or product_data.get('imageUrl')
            or product_data.get('productImageOriginal')
            or ''
        )
        qr_url = product_data.get('qrUrl', product_data.get('nfcUrl', ''))
        product_name = product_data.get('productName', '') or ''
        product_brand = product_data.get('productBrand', '') or ''
        product_desc = product_data.get('productDesc', '') or ''
        product_playlist_override = product_data.get('productPlaylist') if isinstance(product_data, dict) else None

        # Diagnostic: surface which V2 image field actually arrived so we can
        # tell apart cold-UPC first-scan (productImage=null, text-only fallback)
        # from warm-cache (productImage=WebP URL) on every successful recycle.
        self.logger.info(
            "Product image fields — productImage=%r productImageOriginal=%r imageUrl=%r resolved=%r",
            product_data.get('productImage'),
            product_data.get('productImageOriginal'),
            product_data.get('imageUrl'),
            product_image_path,
        )

        if self.display_backend is not None:
            self.display_backend.show_product_display(
                product_image_path=product_image_path,
                qr_url=qr_url,
                nfc_url=nfc_url or None,
                playlist_override=product_playlist_override,
                product_name=product_name,
                product_brand=product_brand,
                product_desc=product_desc,
            )
        elif self.video_player:
            if hasattr(self.video_player, 'hide_deposit_waiting'):
                self.video_player.hide_deposit_waiting()
            if hasattr(self.video_player, 'display_product_image'):
                self.video_player.display_product_image(product_data)

        # Publish success result to AWS
        self._publish_recycle_result(
            barcode=barcode,
            transaction_id=transaction_id,
            status="recycle_success"
        )

        # Start NFC broadcasting with URL from AWS (legacy VLC path only;
        # native/REST backends handle NFC via show_product_display nfc_url arg)
        if nfc_url and self.video_player and hasattr(self.video_player, 'start_nfc_for_transaction'):
            self.video_player.start_nfc_for_transaction(nfc_url, transaction_id)
        elif not nfc_url:
            self.logger.warning("No nfcUrl in AWS response - skipping NFC broadcast")

    def _handle_recycle_failure(self, product_data, barcode: str, transaction_id: str):
        """
        Handle failed recycling verification (item not detected by sensor).

        Called AFTER door is closed when item was NOT detected.
        Shows error message, publishes failure — no QR, no NFC.
        """
        self.logger.warning(f"Recycle FAILURE for barcode: {barcode} - item not detected")
        print("Item was NOT detected by sensor")

        # Show no-item-detected screen on whichever display backend is active.
        # The cloud may have supplied a `noItemPlaylist` override on the openDoor
        # payload — replay it here so V2 payloads can steer the timeout screen
        # without device-side branding logic. None falls back to tsv6_no_item.
        if self.display_backend is not None:
            self.display_backend.show_no_item_detected(
                playlist_override=self._pending_no_item_playlist
            )
        elif self.video_player:
            if hasattr(self.video_player, 'hide_deposit_waiting'):
                self.video_player.hide_deposit_waiting()
            if hasattr(self.video_player, 'display_recycle_failure'):
                self.video_player.display_recycle_failure()

        # Publish failure result to AWS
        self._publish_recycle_result(
            barcode=barcode,
            transaction_id=transaction_id,
            status="recycle_unsuccess"
        )

        self.error_recovery.report_error(
            "recycle_sensor",
            "item_not_detected",
            f"Item not deposited for barcode: {barcode}",
            "low"
        )

    def _publish_recycle_result(self, barcode: str, transaction_id: str, status: str):
        """
        Publish recycling verification result to AWS IoT.

        Uses a dedicated topic ({thing_name}/recycleResult) instead of the shadow
        update topic to avoid triggering the barcode Lambda again — publishing to
        the shadow with a barcode field causes Lambda to respond with another
        openDoor, creating an infinite loop.

        Args:
            barcode: Scanned barcode
            transaction_id: Transaction ID from AWS
            status: "recycle_success" or "recycle_unsuccess"
        """
        try:
            thing_name = self.aws_config["thing_name"]
            recycle_topic = f"{thing_name}/recycleResult"

            result_payload = {
                "thingName": thing_name,
                "barcode": barcode,
                "transactionId": transaction_id,
                "recycleStatus": status,
                "timestampISO": datetime.datetime.utcnow().isoformat() + "Z",
                "deviceType": "raspberry-pi"
            }

            if self.aws_manager and self.aws_manager.connected:
                success = self.aws_manager.publish_with_retry(
                    recycle_topic,
                    result_payload
                )
                if success:
                    self.logger.info(f"Published recycle result: {status}")
                    print(f"Recycle result sent to AWS: {status}")
                else:
                    self.logger.error("Failed to publish recycle result")
            else:
                self.logger.warning("AWS not connected - recycle result not published")

        except Exception as e:
            self.logger.error(f"Error publishing recycle result: {e}")

    def _handle_obstruction_detected(self):
        """
        Handle persistent obstruction after all retries exhausted.

        1. Publishes status update to AWS with connectionState "Device Obstructed"
        2. Starts the obstruction handler service which will:
           - Stop tsv6.service
           - Display UI for user to clear obstruction
           - Close servo and restart service when cleared
        """
        self.logger.critical("Device obstruction detected after 3 retries - door left open")
        print("ALERT: Device obstruction detected - door left open")

        try:
            # Build obstruction status payload
            obstruction_status = {
                "thingName": self.aws_config["thing_name"],
                "connectionState": "Device Obstructed",
                "deviceType": "raspberry-pi",
                "timestampISO": datetime.datetime.utcnow().isoformat() + "Z",
                "obstruction": {
                    "detected_at": datetime.datetime.utcnow().isoformat() + "Z",
                    "retries_attempted": 3,
                    "device_id": self.aws_config["thing_name"],
                    "status": "door_left_open"
                }
            }

            # Publish to AWS IoT shadow
            shadow_payload = {"state": {"reported": obstruction_status}}

            if self.aws_manager and self.aws_manager.connected:
                success = self.aws_manager.publish_with_retry(
                    self.aws_manager.shadow_update_topic,
                    shadow_payload
                )
                if success:
                    self.logger.info("Obstruction status published to AWS")
                    print("Obstruction status published to AWS")
                else:
                    self.logger.error("Failed to publish obstruction status to AWS")
            else:
                self.logger.warning("AWS not connected - obstruction status not published")

            # Report to error recovery system
            self.error_recovery.report_error(
                "servo_controller",
                "obstruction_detected",
                "Device obstructed after 3 retries - door left open",
                "critical"
            )

            # Start the obstruction handler service
            # This will stop tsv6.service, show UI, and restart when cleared
            self._start_obstruction_handler()

        except Exception as e:
            self.logger.error(f"Failed to handle obstruction: {e}")

    def _start_obstruction_handler(self):
        """Start the obstruction handler service to display UI and handle user input"""
        try:
            self.logger.info("Starting obstruction handler service...")
            import subprocess

            # Start the obstruction handler service
            result = subprocess.run(
                ['sudo', 'systemctl', 'start', 'tsv6-obstruction-handler.service'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                self.logger.info("Obstruction handler service started")
                print("Obstruction handler service started - UI will appear shortly")
            else:
                self.logger.error(f"Failed to start obstruction handler: {result.stderr}")
                # Fallback: run directly if service fails
                self._run_obstruction_handler_directly()

        except subprocess.TimeoutExpired:
            self.logger.warning("Timeout starting obstruction handler service, running directly")
            self._run_obstruction_handler_directly()
        except Exception as e:
            self.logger.error(f"Error starting obstruction handler: {e}")
            self._run_obstruction_handler_directly()

    def _run_obstruction_handler_directly(self):
        """Fallback: Run obstruction handler directly as subprocess"""
        try:
            import subprocess
            handler_path = Path(__file__).parent.parent / 'services' / 'obstruction_handler.py'
            self.logger.info(f"Running obstruction handler directly: {handler_path}")

            subprocess.Popen(
                [sys.executable, str(handler_path)],
                env={**os.environ, 'DISPLAY': ':0'},
                start_new_session=True
            )
        except Exception as e:
            self.logger.error(f"Failed to run obstruction handler directly: {e}")

    def _on_no_match_display(self, payload=None):
        """Handle no match display requests.

        Accepts the optional parsed MQTT payload so V2 noMatch responses can
        carry a `noMatchPlaylist` override. V1 payloads (or legacy callers
        passing nothing) end up with playlist_override=None and the display
        backend uses its default tsv6_no_match playlist.
        """
        try:
            playlist_override = (
                payload.get("noMatchPlaylist") if isinstance(payload, dict) else None
            )

            if self.display_backend is not None:
                self.display_backend.show_no_match(playlist_override=playlist_override)
                self.error_recovery.report_success("pisignage")
            elif self.video_player and hasattr(self.video_player, 'display_no_match_image'):
                self.video_player.display_no_match_image()
                self.error_recovery.report_success("video_player")

        except Exception as e:
            self.logger.error(f"Failed to handle no match display: {e}")
            self.error_recovery.report_error("video_player", "no_match_display_failed", str(e), "medium")

    def _on_cloud_qr_code_display(self, payload=None):
        """Handle cloud-emitted qrCode messages.

        Distinct from `_display_qr_not_allowed_image`, which is invoked locally
        by the barcode scanner the moment a QR is decoded. This callback is
        invoked when the *cloud* classifies a scanned code as a QR via the V2
        Lambda flow (BarcodeRepoLookupV2 / UpdatedBarcodeToGoUPCV2). The cloud
        may attach a `barcodeNotQrPlaylist` override; V1 deployments will not
        publish to this topic at all.
        """
        try:
            playlist_override = (
                payload.get("barcodeNotQrPlaylist") if isinstance(payload, dict) else None
            )

            if self.display_backend is not None:
                self.display_backend.show_barcode_not_qr(playlist_override=playlist_override)
                self.error_recovery.report_success("pisignage")
            elif (
                self.video_player
                and hasattr(self.video_player, 'display_qr_not_allowed_image')
                and getattr(self.video_player, 'root', None)
            ):
                self.video_player.root.after(0, self.video_player.display_qr_not_allowed_image)
            else:
                self.logger.debug(
                    "Cloud qrCode received before UI init; skipping display"
                )
            self.error_recovery.report_success("qr_code_detection")
        except Exception as e:
            self.logger.error(f"Failed to handle cloud qrCode display: {e}")
            self.error_recovery.report_error("qr_code_detection", "qr_display", str(e), "medium")

    def _on_barcode_scanned(self, barcode_data, transaction_id):
        """Handle barcode scan events"""
        try:
            self.logger.info(f"Barcode scanned: {barcode_data}")

            # Show the deposit-item playlist immediately on scan and let it
            # loop for the entire transaction (AWS lookup, door open/close).
            # The success path (_handle_recycle_success) and failure paths
            # (_handle_recycle_failure / no_match / barcode_not_qr) swap to
            # their own screens, which ends the loop.
            if self.display_backend is not None:
                self.display_backend.show_deposit_item()
            elif self.video_player and hasattr(self.video_player, 'next_video'):
                # Legacy VLC: advance to the next video as the processing signal
                self.video_player.root.after(0, self.video_player.next_video)

            self.error_recovery.report_success("barcode_scanner")

        except Exception as e:
            self.logger.error(f"Failed to handle barcode scan: {e}")
            self.error_recovery.report_error("barcode_scanner", "scan_processing", str(e), "medium")
    
    def _display_qr_not_allowed_image(self, qr_data):
        """Handle QR code detection by displaying barcode_not_qr.jpg image"""
        try:
            self.logger.info(f"QR Code detected: {qr_data}")

            if self.display_backend is not None:
                self.display_backend.show_barcode_not_qr()
            elif (
                self.video_player
                and hasattr(self.video_player, 'display_qr_not_allowed_image')
                and getattr(self.video_player, 'root', None)
            ):
                self.video_player.root.after(0, self.video_player.display_qr_not_allowed_image)
            else:
                # UI may not be initialized yet; skip/defer to avoid None access
                self.logger.debug("QR detected before UI init; skipping QR-not-allowed display")

            self.error_recovery.report_success("qr_code_detection")

        except Exception as e:
            self.logger.error(f"Failed to handle QR code detection: {e}")
            self.error_recovery.report_error("qr_code_detection", "qr_display", str(e), "medium")
    
    def _handle_video_player_exit(self):
        """Handle video player exit requests"""
        self.logger.info("Video player exit requested")
        self.shutdown()
    
    def start(self):
        """Start the enhanced production system"""
        if self.running:
            return
        
        self.logger.info("Starting enhanced production system...")
        self.running = True
        
        try:
            # Start monitoring systems
            if self.network_monitor:
                self.network_monitor.start()

            # Start LTE monitor if available
            if self.lte_monitor:
                self.lte_monitor.start()

            # Start connectivity manager if available
            if self.connectivity_manager:
                self.connectivity_manager.start()

            if self.health_monitor:
                self.health_monitor.start()

            # Start bin level monitor if available
            if self.bin_level_monitor:
                self.bin_level_monitor.start()

            # Start connection deadline monitoring
            self.connection_deadline_monitor.start()
            self.network_deadline_monitor.start()

            # Connect AWS manager
            if self.aws_manager:
                # Wire bin level data provider to AWS status publishes
                if self.bin_level_monitor:
                    self.aws_manager.set_bin_level_provider(
                        self.bin_level_monitor.get_latest_fill_data
                    )

                self.aws_manager.connect()
                self.aws_manager.start_auto_reconnect()

                # Watchdog monitoring disabled - was causing errors
                # if self.watchdog_monitor and self.watchdog_monitor.unexpected_restart:
                #     self.logger.warning("⚠️  Unexpected restart detected")
                #     watchdog_info = self.watchdog_monitor.get_restart_info()
                # if self.watchdog_monitor:
                #     self.watchdog_monitor.save_boot_id()
            
            # Start barcode scanning
            if self.barcode_scanner:
                self.barcode_scanner.start_scanning()

            # Connect barcode callbacks (regardless of display mode)
            if self.barcode_scanner:
                self.barcode_scanner.barcode_callback = self._on_barcode_scanned
                self.barcode_scanner.qr_code_callback = self._display_qr_not_allowed_image

            # Start display — either PiSignage/Native (headless) or VLC (tkinter)
            if self._pisignage_enabled and self.display_backend is not None:
                # Backend mode: show idle attract loop and run headless event loop
                self.display_backend.show_idle()

                # Backwards-compat: REST adapter also accepts set_default_playlist
                if self.pisignage_adapter is not None:
                    self.pisignage_adapter.set_default_playlist()

                self.logger.info("Enhanced production system fully started (display backend mode)")
                print("Enhanced Production System Ready (Display Backend)")
                if self.pisignage_adapter is not None:
                    print(f"PiSignage Server: {self.pisignage_adapter.server_url}")
                    print(f"PiSignage Player: {self.pisignage_adapter.player_id}")
                print(f"Error Recovery: {len(self.error_recovery.component_health)} components monitored")
                if self.network_monitor:
                    print(f"Network Monitor: {self.network_monitor.cfg.interface} monitoring enabled")
                if self.lte_controller:
                    print(f"LTE Controller: {self.lte_controller.config.apn} APN configured")
                print(f"Network Adapter: {os.getenv('TSV6_NETWORK_ADAPTER', 'rpi-wifi')}")
                if self.connectivity_manager:
                    print(f"Connectivity Mode: {self.connectivity_manager.config.mode.value}")
                print(f"AWS IoT: {self.aws_config['thing_name']} ready")

                # Start long-press touchscreen gesture (evdev-level, bypasses
                # Chromium DOM which is unreliable when VLC's Tk X11 window
                # sits above Chromium in the stacking order). Only meaningful
                # for the native backend which runs Chromium+VLC locally.
                self._start_long_press_watcher()

                # Headless main loop — wait for shutdown signal
                try:
                    self.shutdown_event.wait()
                except KeyboardInterrupt:
                    pass

            elif self.video_player:
                # Legacy VLC mode with tkinter mainloop
                self.video_player.setup_video_display()
                self.video_player.load_videos()
                self.video_player.start_status_publishing()

                # Start video playback
                if self.video_player.video_files:
                    self.video_player.play_current_video()

                self.logger.info("Enhanced production system fully started (VLC mode)")
                print("Enhanced Production System Ready (VLC Display)")
                print(f"Error Recovery: {len(self.error_recovery.component_health)} components monitored")
                if self.network_monitor:
                    print(f"Network Monitor: {self.network_monitor.cfg.interface} monitoring enabled")
                if self.lte_controller:
                    print(f"LTE Controller: {self.lte_controller.config.apn} APN configured")
                print(f"Network Adapter: {os.getenv('TSV6_NETWORK_ADAPTER', 'rpi-wifi')}")
                if self.connectivity_manager:
                    print(f"Connectivity Mode: {self.connectivity_manager.config.mode.value}")
                print(f"AWS IoT: {self.aws_config['thing_name']} ready")

                # Run tkinter main loop
                try:
                    self.video_player.root.mainloop()
                except KeyboardInterrupt:
                    pass
            
        except Exception as e:
            self.logger.critical(f"Failed to start production system: {e}")
            self.error_recovery.report_error("system", "startup_failure", str(e), "critical")
        finally:
            self.shutdown()

    def _toggle_vlc_window(self, hide: bool) -> None:
        """Unmap (hide=True) or re-map (hide=False) the VLC Tk X11 window.

        VLC renders in a sibling Tk window that sits ABOVE Chromium in the
        stacking order (that is how video shows through the transparent #main
        zone on the router page).  XConfigureWindow(Above) is ignored by the
        compositor on the Pi, so for settings visibility we UNMAP the Tk
        window entirely.  VLC keeps running underneath, invisible; when the
        user exits settings we map the Tk window back and VLC resumes visible
        playback.

        IMPORTANT: There can be multiple Tk windows on screen at once
        (status indicator overlay = 4x24 px tk, VLC = full-screen tk #2).
        We must target the LARGEST tk-class window — the small overlays
        should stay visible (they convey connection state to the user).
        """
        try:
            from Xlib import display as xdisplay
            d = xdisplay.Display()
            root = d.screen().root

            tk_windows: list = []  # (area, geometry, window) tuples

            def walk(w):
                try:
                    cls = w.get_wm_class()
                except Exception:
                    cls = None
                # Match any tk* class (e.g. "tk", "tk #2", ...).
                if cls and isinstance(cls[0], str) and cls[0].startswith("tk"):
                    try:
                        geom = w.get_geometry()
                        tk_windows.append((geom.width * geom.height, geom, w))
                    except Exception:
                        pass
                try:
                    for c in w.query_tree().children:
                        walk(c)
                except Exception:
                    pass

            walk(root)

            if not tk_windows:
                self.logger.warning(
                    "VLC Tk window not found; skip %s", "unmap" if hide else "map"
                )
                d.close()
                return

            # Pick the largest by area — that's the fullscreen VLC window.
            tk_windows.sort(key=lambda t: t[0], reverse=True)
            area, geom, win = tk_windows[0]

            if hide:
                win.unmap()
            else:
                win.map()
            d.sync()
            d.close()

            self.logger.info(
                "VLC Tk window %s (size=%dx%d, area=%d, candidates=%d)",
                "unmapped" if hide else "mapped",
                geom.width, geom.height, area, len(tk_windows),
            )
        except Exception:
            self.logger.exception("Toggle VLC window failed")

    def _open_settings(self) -> None:
        """Long-press callback: hide VLC and navigate Chromium to /settings."""
        self._toggle_vlc_window(hide=True)
        try:
            import json
            import urllib.request

            import websocket

            pages = json.loads(
                urllib.request.urlopen(
                    "http://127.0.0.1:9222/json", timeout=2
                ).read()
            )
            tgt = next(
                (
                    p
                    for p in pages
                    if p.get("type") == "page" and "8765" in p.get("url", "")
                ),
                None,
            )
            if not tgt:
                self.logger.warning("Long-press: no kiosk page found via CDP")
                return
            ws = websocket.create_connection(
                tgt["webSocketDebuggerUrl"],
                timeout=2,
                origin="http://localhost:9222",
            )
            ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Page.navigate",
                        "params": {"url": "http://127.0.0.1:8765/settings"},
                    }
                )
            )
            ws.recv()
            ws.close()
            self.logger.info("Long-press: navigated kiosk to /settings")
        except Exception:
            self.logger.exception("Long-press: CDP navigate failed")

    def _resume_from_settings(self) -> None:
        """Wake callback: restore the idle display after user exits settings."""
        self._toggle_vlc_window(hide=False)
        try:
            if self.display_backend is None:
                self.logger.warning("Settings exit: no display backend available")
                return
            if not self.display_backend.show_idle():
                self.logger.warning("Settings exit: backend.show_idle() returned false")
            else:
                self.logger.info("Settings exit: idle display restarted")
        except Exception:
            self.logger.exception("Settings exit: failed to restart idle display")

    def _wire_settings_wake_callback(self) -> bool:
        """Wire POST /api/exit-settings to restart idle/Vengo playback."""
        renderer = getattr(self.display_backend, "_renderer", None)
        router = getattr(renderer, "_router", None) if renderer else None
        if router is not None and hasattr(router, "set_wake_callback"):
            router.set_wake_callback(self._resume_from_settings)
            self.logger.info("Settings wake callback wired")
            return True

        self.logger.warning("Settings wake callback not wired: router unavailable")
        return False

    def _wire_settings_motor_callback(self) -> bool:
        """Wire /api/motor/* settings endpoints to the live servo controller."""
        setter = getattr(self.display_backend, "set_motor_callback", None)
        if callable(setter) and setter(self._handle_motor_setup_command):
            self.logger.info("Settings motor callback wired")
            return True

        renderer = getattr(self.display_backend, "_renderer", None)
        router = getattr(renderer, "_router", None) if renderer else None
        if router is not None and hasattr(router, "set_motor_callback"):
            router.set_motor_callback(self._handle_motor_setup_command)
            self.logger.info("Settings motor callback wired via router")
            return True

        self.logger.warning("Settings motor callback not wired: router unavailable")
        return False

    def _handle_motor_setup_command(self, action: str, payload: dict) -> dict:
        """Handle motor setup commands from the local settings UI."""
        if self.servo_controller is None:
            return {
                "ok": False,
                "available": False,
                "error": "servo controller is not initialized",
                "status": 503,
            }

        try:
            if action == "status":
                return {
                    "ok": True,
                    "available": True,
                    "calibration": self._servo_calibration_snapshot(),
                }

            with self._door_sequence_lock:
                if self._door_sequence_active:
                    return {
                        "ok": False,
                        "available": True,
                        "error": "door sequence is active",
                        "status": 409,
                    }
                self._door_sequence_active = True

            try:
                if action == "move":
                    return self._handle_motor_move(payload)
                if action == "calibration":
                    return self._handle_motor_calibration(payload)
                return {
                    "ok": False,
                    "available": True,
                    "error": f"unknown motor action: {action}",
                    "status": 400,
                }
            finally:
                with self._door_sequence_lock:
                    self._door_sequence_active = False

        except Exception as exc:
            self.logger.exception("Motor setup command failed: %s", action)
            return {
                "ok": False,
                "available": True,
                "error": str(exc),
                "status": 500,
            }

    def _servo_calibration_snapshot(self) -> dict:
        """Return calibration data using the controller helper when available."""
        getter = getattr(self.servo_controller, "get_calibration", None)
        if callable(getter):
            return getter()
        return {
            "open_position": getattr(self.servo_controller, "open_position", None),
            "closed_position": getattr(self.servo_controller, "closed_position", None),
            "current_position": self.servo_controller.get_position()
            if hasattr(self.servo_controller, "get_position") else None,
            "connected": bool(getattr(self.servo_controller, "is_connected", False)),
            "simulation": bool(getattr(self.servo_controller, "simulation_mode", False)),
        }

    def _handle_motor_move(self, payload: dict) -> dict:
        """Move the motor to open/closed or a raw position from settings."""
        target = str(payload.get("target") or "").strip().lower()
        ok = False
        if target == "open":
            ok = bool(self.servo_controller.open_door(hold_time=0))
        elif target == "closed":
            ok = bool(self.servo_controller.close_door(hold_time=0.2))
        elif target == "position":
            position = self._parse_servo_position(payload.get("position"))
            enable = getattr(self.servo_controller, "_enable_torque", None)
            if callable(enable):
                enable(True)
            ok = bool(self.servo_controller._set_position(position))
            wait = getattr(self.servo_controller, "_wait_for_movement", None)
            if callable(wait):
                wait()
        else:
            return {
                "ok": False,
                "available": True,
                "error": "target must be open, closed, or position",
                "status": 400,
            }

        return {
            "ok": ok,
            "available": True,
            "calibration": self._servo_calibration_snapshot(),
            "error": None if ok else "servo move failed",
            "status": 200 if ok else 500,
        }

    def _handle_motor_calibration(self, payload: dict) -> dict:
        """Update and persist motor open/closed calibration values."""
        updates = {}
        use_current_for = str(payload.get("use_current_for") or "").strip().lower()
        if use_current_for:
            current = self.servo_controller.get_position()
            if use_current_for == "open":
                updates["open_position"] = current
            elif use_current_for == "closed":
                updates["closed_position"] = current
            else:
                return {
                    "ok": False,
                    "available": True,
                    "error": "use_current_for must be open or closed",
                    "status": 400,
                }

        if "open_position" in payload and payload.get("open_position") is not None:
            updates["open_position"] = self._parse_servo_position(payload.get("open_position"))
        if "closed_position" in payload and payload.get("closed_position") is not None:
            updates["closed_position"] = self._parse_servo_position(payload.get("closed_position"))

        if not updates:
            return {
                "ok": False,
                "available": True,
                "error": "no calibration values supplied",
                "status": 400,
            }

        setter = getattr(self.servo_controller, "set_calibration", None)
        if callable(setter):
            calibration = setter(**updates, persist=True)
        else:
            for key, value in updates.items():
                setattr(self.servo_controller, key, value)
            calibration = self._servo_calibration_snapshot()

        self.logger.info("Servo calibration updated from settings: %s", updates)
        return {
            "ok": True,
            "available": True,
            "calibration": calibration,
        }

    @staticmethod
    def _parse_servo_position(value) -> int:
        """Parse a raw servo position from settings input."""
        try:
            position = int(value)
        except (TypeError, ValueError):
            raise ValueError("position must be an integer")
        if position < 0 or position > 4095:
            raise ValueError("position must be between 0 and 4095")
        return position

    def _start_long_press_watcher(self) -> None:
        """Start the evdev-level long-press gesture watcher.

        Only meaningful when the native TSV6NativeBackend is active (which
        runs Chromium + VLC locally).  The REST backend (PiSignageAdapter)
        talks to a remote player — long-press to open local /settings would
        have no effect there.
        """
        if not LONGPRESS_AVAILABLE:
            self.logger.warning(
                "LongPressWatcher not available — long-press gesture disabled"
            )
            return

        # Only wire up for the native backend (Chromium+VLC running locally).
        # Guard the isinstance check so it doesn't NameError when the native
        # backend module failed to import (TSV6_NATIVE_AVAILABLE=False).
        if not TSV6_NATIVE_AVAILABLE or not isinstance(
            self.display_backend, TSV6NativeBackend
        ):
            self.logger.info(
                "Long-press skipped: display backend is not TSV6NativeBackend"
            )
            return

        hold_seconds = float(os.environ.get("TSV6_LONGPRESS_SECONDS", "5"))

        # The settings wake callback is wired during native backend init so
        # Close works even if the long-press watcher fails to start.

        self._long_press_watcher = LongPressWatcher(
            self._open_settings, hold_seconds=hold_seconds
        )
        started = self._long_press_watcher.start()
        if started:
            self.logger.info(
                "Long-press watcher started (hold=%.1fs)", hold_seconds
            )
        else:
            self.logger.warning(
                "Long-press watcher failed to start (no touchscreen device?)"
            )

    def shutdown(self):
        """Graceful shutdown of all systems"""
        if not self.running:
            return
        
        self.logger.info("Shutting down enhanced production system...")
        self.running = False
        self.shutdown_event.set()
        
        try:
            # Stop PiSignage health monitor
            if self.pisignage_health_monitor:
                try:
                    self.pisignage_health_monitor.stop()
                    self.logger.info("PiSignage health monitor stopped")
                except Exception as e:
                    self.logger.warning(f"Error stopping PiSignage health monitor: {e}")

            # Stop the unified display backend (native or REST)
            if self.display_backend is not None and self.display_backend is not self.pisignage_adapter:
                try:
                    self.display_backend.stop()
                    self.display_backend.disconnect()
                    self.logger.info("Display backend stopped")
                except Exception as e:
                    self.logger.warning(f"Error stopping display backend: {e}")

            # Disconnect PiSignage adapter (REST backend)
            if self.pisignage_adapter:
                try:
                    self.pisignage_adapter.disconnect()
                    self.logger.info("PiSignage adapter disconnected")
                except Exception as e:
                    self.logger.warning(f"Error disconnecting PiSignage: {e}")

            # Stop barcode scanning
            if self.barcode_scanner:
                self.barcode_scanner.stop_scanning()

            # Stop long-press gesture watcher
            if self._long_press_watcher:
                try:
                    self._long_press_watcher.stop()
                    self.logger.info("Long-press watcher stopped")
                except Exception as e:
                    self.logger.warning(f"Error stopping long-press watcher: {e}")

            # Stop AWS manager
            if self.aws_manager:
                self.aws_manager.disconnect()
            
            # Stop monitoring systems
            if self.bin_level_monitor:
                self.bin_level_monitor.stop()

            if self.tof_sensor:
                self.tof_sensor.cleanup()

            if self.connectivity_manager:
                self.connectivity_manager.stop()

            if self.lte_monitor:
                self.lte_monitor.stop()

            if self.lte_controller:
                self.lte_controller.cleanup()

            if self.network_monitor:
                self.network_monitor.stop()

            if self.health_monitor:
                self.health_monitor.stop()

            # Stop connection deadline monitoring
            if self.connection_deadline_monitor:
                self.connection_deadline_monitor.stop()
            if self.network_deadline_monitor:
                self.network_deadline_monitor.stop()
            
            # Stop error recovery
            if self.error_recovery:
                self.error_recovery.stop()
            
            # Cleanup servo
            if self.servo_controller:
                self.servo_controller.cleanup()

            # Cleanup recycle sensor
            if self.recycle_sensor:
                self.recycle_sensor.cleanup()

            # Stop video player
            if self.video_player and hasattr(self.video_player, 'root') and self.video_player.root:
                try:
                    self.video_player.root.quit()
                    self.video_player.root.destroy()
                except:
                    pass
            
            self.logger.info("✅ Enhanced production system shutdown complete")
            
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")
    
    def get_system_status(self):
        """Get comprehensive system status"""
        status = {
            "running": self.running,
            "environment": self.config_manager.environment.value,
            "device_info": self.config_manager.device_info,
            "components": {}
        }
        
        # Enhanced error recovery status
        if self.error_recovery:
            status["error_recovery"] = self.error_recovery.get_system_health_status()
        
        # Network status with recovery info
        if self.network_monitor:
            status["network"] = {
                "status": getattr(self.network_monitor, '_last_connected', None),
                "recovery_status": self.network_monitor.get_recovery_status()
            }
        
        # Health status
        if self.health_monitor:
            status["health"] = self.health_monitor.get_health_summary()
        
        # AWS status
        if self.aws_manager:
            status["aws"] = self.aws_manager.get_status()
        
        # Connection tracking metrics
        if self.connection_tracker:
            status["connection_metrics"] = self.connection_tracker.get_status_summary()
        
        if self.connection_deadline_monitor:
            status["deadline_monitor"] = {
                "disconnection_duration_minutes": self.connection_deadline_monitor.get_disconnection_duration_minutes(),
                "deadline_minutes": self.connection_deadline_monitor.deadline_minutes,
                "deadline_exceeded": self.connection_deadline_monitor.deadline_exceeded
            }
        if self.network_deadline_monitor:
            status["network_deadline_monitor"] = {
                "disconnection_duration_minutes": self.network_deadline_monitor.get_disconnection_duration_minutes(),
                "deadline_minutes": self.network_deadline_monitor.deadline_minutes,
                "deadline_exceeded": self.network_deadline_monitor.deadline_exceeded
            }

        if self.bin_level_monitor:
            status["bin_level"] = self.bin_level_monitor.get_monitor_status()

        return status
    
    def _on_connection_deadline_exceeded(self, downtime_minutes: float):
        """Handle connection deadline exceeded"""
        self.logger.critical(
            f"Connection deadline exceeded! Disconnected for {downtime_minutes:.1f} minutes. "
            f"System will reboot shortly."
        )
        
        # Report to error recovery system
        self.error_recovery.report_error(
            "aws_connection",
            "deadline_exceeded",
            f"Disconnected for {downtime_minutes:.1f} minutes - forcing reboot",
            "critical"
        )

    def _on_network_deadline_exceeded(self, downtime_minutes: float):
        """Handle sustained network outage deadline exceeded."""
        self.logger.critical(
            f"Network failure deadline exceeded! Network unreachable for {downtime_minutes:.1f} minutes. "
            f"System will cleanly reboot shortly."
        )

        self.error_recovery.report_error(
            "network",
            "deadline_exceeded",
            f"Network unreachable for {downtime_minutes:.1f} minutes - clean reboot requested",
            "critical"
        )


def main():
    """Main entry point for enhanced production system"""
    production_player = None
    
    try:
        production_player = ProductionVideoPlayer()
        production_player.start()
        # If start() returns normally, keep the process alive (don't exit with 0)
        # This prevents the service from restarting on successful runs
        print("✅ Production system completed main loop - keeping process alive")
        import time
        while True:
            time.sleep(1)
        
    except KeyboardInterrupt:
        print("\n🛑 Shutdown requested by user")
        if production_player:
            production_player.shutdown()
        sys.exit(0)
    except Exception as e:
        print(f"❌ Critical error in main: {e}")
        import traceback
        traceback.print_exc()
        if production_player:
            production_player.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
