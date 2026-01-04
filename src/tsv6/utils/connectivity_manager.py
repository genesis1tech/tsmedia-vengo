#!/usr/bin/env python3
"""
Connectivity Manager for TSV6 Raspberry Pi

Manages network connectivity with WiFi/LTE failover support.
Provides unified interface for AWS IoT, OTA updates, and general connectivity.

Default mode: LTE Primary + WiFi Backup
"""

import os
import threading
import time
import logging
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Dict, Any, TYPE_CHECKING
from enum import Enum

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .network_monitor import NetworkMonitor
    from .lte_monitor import LTEMonitor
    from .error_recovery import ErrorRecoverySystem


class ConnectivityMode(Enum):
    """Network connectivity mode configuration"""
    WIFI_ONLY = "wifi_only"
    LTE_ONLY = "lte_only"
    WIFI_PRIMARY_LTE_BACKUP = "wifi_primary_lte_backup"
    LTE_PRIMARY_WIFI_BACKUP = "lte_primary_wifi_backup"


class ConnectionType(Enum):
    """Active connection type"""
    NONE = "none"
    WIFI = "wifi"
    LTE = "lte"


@dataclass
class ConnectivityManagerConfig:
    """Configuration for connectivity management"""
    # Default: LTE Primary + WiFi Backup
    mode: ConnectivityMode = ConnectivityMode.LTE_PRIMARY_WIFI_BACKUP

    # Failover timing
    failover_timeout_secs: float = 60.0       # Time before switching to backup
    failback_check_interval_secs: float = 300.0  # How often to check if primary recovered
    failback_stability_secs: float = 30.0     # Primary must be stable before switching back

    # Status reporting
    status_report_interval_secs: float = 900.0  # 15 minutes (saves LTE data costs)

    # Connection priorities (higher = preferred when both available)
    wifi_priority: int = 100
    lte_priority: int = 200  # LTE preferred by default

    # Power saving: disconnect backup when primary is active
    disable_backup_when_primary_active: bool = True

    # WiFi connection name in NetworkManager
    wifi_connection_name: str = ""  # Auto-detected if empty

    # Startup behavior: wait for LTE before enabling WiFi
    # If LTE is primary, disable WiFi at startup and wait for LTE to connect
    lte_startup_wait_secs: float = 90.0  # Time to wait for LTE before enabling WiFi fallback

    # Splash screen settings
    show_lte_splash: bool = True  # Show splash screen during LTE wait
    lte_splash_image: str = "/home/g1tech/tsrpi5/event_images/g1tech.jpg"
    lte_splash_text: str = "Please wait connecting to 4G LTE"


class ConnectivityManager:
    """
    Manages network connectivity with WiFi/LTE failover.

    Provides:
    - Unified interface for connection status
    - Automatic failover between WiFi and LTE
    - Automatic failback when primary connection recovers
    - Status reporting for AWS IoT

    Usage:
        manager = ConnectivityManager(
            config=ConnectivityManagerConfig(),
            wifi_monitor=wifi_mon,
            lte_monitor=lte_mon,
        )
        manager.start()

        if manager.is_connected():
            print(f"Connected via: {manager.get_active_connection()}")

        manager.stop()
    """

    def __init__(
        self,
        config: Optional[ConnectivityManagerConfig] = None,
        wifi_monitor: Optional['NetworkMonitor'] = None,
        lte_monitor: Optional['LTEMonitor'] = None,
        error_recovery_system: Optional['ErrorRecoverySystem'] = None,
        on_connection_change: Optional[Callable[[ConnectionType, ConnectionType], None]] = None,
        on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_lte_wait_start: Optional[Callable[[str, str], None]] = None,  # (image_path, text)
        on_lte_wait_end: Optional[Callable[[bool], None]] = None,  # (success: bool)
    ):
        """
        Initialize connectivity manager.

        Args:
            config: Configuration (uses defaults if None)
            wifi_monitor: WiFi network monitor instance
            lte_monitor: LTE network monitor instance
            error_recovery_system: Optional error recovery integration
            on_connection_change: Callback(old_type, new_type) on active connection change
            on_status: Callback for periodic status updates
            on_lte_wait_start: Callback(image_path, text) when LTE startup wait begins
            on_lte_wait_end: Callback(success) when LTE startup wait ends
        """
        self.config = config or ConnectivityManagerConfig()
        self.wifi_monitor = wifi_monitor
        self.lte_monitor = lte_monitor
        self.error_recovery = error_recovery_system
        self.on_connection_change = on_connection_change
        self.on_status = on_status
        self.on_lte_wait_start = on_lte_wait_start
        self.on_lte_wait_end = on_lte_wait_end

        # State tracking
        self._active_connection = ConnectionType.NONE
        self._wifi_connected = False
        self._lte_connected = False
        self._failover_in_progress = False
        self._last_failover_time = 0
        self._primary_failure_start = 0

        # Thread safety
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Status tracking
        self._wifi_status: Dict[str, Any] = {}
        self._lte_status: Dict[str, Any] = {}

        # Determine primary/backup based on mode
        self._primary, self._backup = self._get_connection_order()

        # WiFi connection name for NetworkManager control
        self._wifi_conn_name = self.config.wifi_connection_name or self._detect_wifi_connection()
        self._wifi_disabled_by_us = False

        logger.info(f"ConnectivityManager initialized (mode: {self.config.mode.value})")
        logger.info(f"Primary: {self._primary.value}, Backup: {self._backup.value if self._backup else 'none'}")
        if self.config.disable_backup_when_primary_active:
            logger.info(f"Power saving enabled: backup will be disabled when primary is active")
            if self._wifi_conn_name:
                logger.info(f"WiFi connection name: {self._wifi_conn_name}")

    def _get_connection_order(self) -> tuple:
        """Determine primary and backup connections based on mode"""
        mode = self.config.mode

        if mode == ConnectivityMode.WIFI_ONLY:
            return ConnectionType.WIFI, None
        elif mode == ConnectivityMode.LTE_ONLY:
            return ConnectionType.LTE, None
        elif mode == ConnectivityMode.WIFI_PRIMARY_LTE_BACKUP:
            return ConnectionType.WIFI, ConnectionType.LTE
        elif mode == ConnectivityMode.LTE_PRIMARY_WIFI_BACKUP:
            return ConnectionType.LTE, ConnectionType.WIFI
        else:
            return ConnectionType.LTE, ConnectionType.WIFI

    def _detect_wifi_connection(self) -> str:
        """Auto-detect WiFi connection name from NetworkManager"""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if ':wifi' in line or ':802-11-wireless' in line:
                        name = line.split(':')[0]
                        if name and name != 'lo':
                            logger.info(f"Auto-detected WiFi connection: {name}")
                            return name
        except Exception as e:
            logger.warning(f"Failed to detect WiFi connection: {e}")
        return ""

    def _log_network_snapshot(self, reason: str) -> None:
        """Log NetworkManager + routing snapshot for debugging decisions.

        Logging-only helper to validate:
        - which WiFi connection (if any) is actually active
        - whether WiFi radio is enabled
        - which interface owns the default route
        """
        try:
            cmds: list[tuple[str, list[str], float]] = [
                (
                    "nmcli_device_status",
                    ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"],
                    10,
                ),
                (
                    "nmcli_active_connections",
                    ["nmcli", "-t", "-f", "NAME,TYPE,STATE,DEVICE", "connection", "show", "--active"],
                    10,
                ),
                (
                    "nmcli_general_status",
                    ["nmcli", "-t", "general", "status"],
                    10,
                ),
                (
                    "ip_default_route",
                    ["ip", "route", "show", "default"],
                    5,
                ),
            ]

            parts: list[str] = []
            for label, cmd, timeout in cmds:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                out = (p.stdout or "").strip()
                err = (p.stderr or "").strip()
                parts.append(
                    f"{label}: rc={p.returncode} out={out!r} err={err!r}"
                )

            logger.info(
                "ConnectivityManager snapshot (%s): active=%s wifi_disabled_by_us=%s wifi_conn_name=%r | %s",
                reason,
                self._active_connection.value,
                self._wifi_disabled_by_us,
                self._wifi_conn_name,
                " | ".join(parts),
            )
        except Exception as e:
            logger.warning("ConnectivityManager snapshot failed (%s): %s", reason, e)

    def _enable_wifi(self) -> bool:
        """Enable WiFi (radio + connection) via NetworkManager.

        This re-enables WiFi when failing over from LTE or when LTE is unavailable.
        """
        try:
            self._log_network_snapshot("before_enable_wifi")
            logger.info(f"Enabling WiFi (radio + connection: {self._wifi_conn_name})")

            # Step 1: Enable WiFi radio first
            result = subprocess.run(
                ["sudo", "nmcli", "radio", "wifi", "on"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                logger.error(f"Failed to enable WiFi radio: {result.stderr}")
                return False

            logger.info("WiFi radio enabled")
            time.sleep(2)  # Wait for radio to initialize and scan for networks

            # Step 2: Notify network monitor that WiFi is no longer intentionally disabled
            # Do this before bringing up connection so monitor knows to track WiFi
            self._wifi_disabled_by_us = False
            if self.wifi_monitor and hasattr(self.wifi_monitor, 'set_wifi_intentionally_disabled'):
                self.wifi_monitor.set_wifi_intentionally_disabled(False)

            # Step 3: Bring up the WiFi connection if we have a connection name
            if self._wifi_conn_name:
                result = subprocess.run(
                    ["sudo", "nmcli", "connection", "up", self._wifi_conn_name],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    logger.info(f"WiFi connection '{self._wifi_conn_name}' activated")
                    self._log_network_snapshot("after_enable_wifi_success")
                    return True
                else:
                    logger.warning(f"WiFi radio enabled but connection failed: {result.stderr}")
                    # Radio is on, so WiFi might still auto-connect
                    self._log_network_snapshot("after_enable_wifi_partial")
                    return True  # Partial success - radio is on
            else:
                logger.info("WiFi radio enabled (no specific connection configured)")
                self._log_network_snapshot("after_enable_wifi_radio_only")
                return True

        except Exception as e:
            logger.error(f"Error enabling WiFi: {e}")
            return False

    def _disable_wifi(self) -> bool:
        """Disable WiFi completely (connection + radio) via NetworkManager.

        This ensures WiFi stays disabled and cannot auto-reconnect when LTE is primary.
        Disabling the radio is more reliable than just disconnecting the connection.
        """
        try:
            self._log_network_snapshot("before_disable_wifi")
            logger.info(f"Disabling WiFi (connection: {self._wifi_conn_name}, radio: will be disabled)")

            # Step 1: Bring down the WiFi connection if we have a connection name
            if self._wifi_conn_name:
                result = subprocess.run(
                    ["sudo", "nmcli", "connection", "down", self._wifi_conn_name],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    logger.info(f"WiFi connection '{self._wifi_conn_name}' deactivated")
                elif "not active" not in result.stderr.lower() and "not an active" not in result.stderr.lower():
                    logger.warning(f"Failed to deactivate WiFi connection: {result.stderr}")
                # Continue even if connection down failed - we'll disable the radio

            # Step 2: Disable WiFi radio to prevent auto-reconnect
            result = subprocess.run(
                ["sudo", "nmcli", "radio", "wifi", "off"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                logger.info("WiFi radio disabled - WiFi completely off")
                self._wifi_disabled_by_us = True
                # Notify network monitor that WiFi is intentionally disabled (LTE-first mode)
                # This prevents network monitor from triggering WiFi provisioning or recovery
                if self.wifi_monitor and hasattr(self.wifi_monitor, 'set_wifi_intentionally_disabled'):
                    self.wifi_monitor.set_wifi_intentionally_disabled(True)
                self._log_network_snapshot("after_disable_wifi_success")
                return True
            else:
                logger.error(f"Failed to disable WiFi radio: {result.stderr}")
                self._log_network_snapshot("after_disable_wifi_failure")
                return False

        except Exception as e:
            logger.error(f"Error disabling WiFi: {e}")
            return False

    def _is_wifi_active(self) -> bool:
        """Check if WiFi is currently active via NetworkManager"""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE,STATE", "connection", "show", "--active"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if 'wifi' in line.lower() or '802-11-wireless' in line.lower():
                        return True
        except Exception:
            pass
        return False

    def _is_wifi_radio_enabled(self) -> bool:
        """Check if WiFi radio is enabled via NetworkManager"""
        try:
            result = subprocess.run(
                ["nmcli", "radio", "wifi"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip().lower() == "enabled"
        except Exception as e:
            logger.warning(f"Failed to check WiFi radio status: {e}")
        return True  # Assume enabled on error to be safe

    def _is_wifi_hotspot_active(self) -> bool:
        """Check if WiFi hotspot (hostapd) is currently running"""
        try:
            result = subprocess.run(
                ["pgrep", "-x", "hostapd"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _stop_wifi_hotspot(self) -> bool:
        """Stop WiFi hotspot (hostapd and dnsmasq) if running"""
        if not self._is_wifi_hotspot_active():
            logger.debug("WiFi hotspot not running, nothing to stop")
            return True

        try:
            logger.info("Stopping WiFi hotspot (hostapd/dnsmasq)...")

            # Stop the provisioning service if it's running
            subprocess.run(
                ["sudo", "systemctl", "stop", "tsv6-wifi-provisioning.service"],
                capture_output=True, timeout=10
            )

            # Kill hostapd and dnsmasq processes directly as backup
            subprocess.run(["sudo", "killall", "hostapd"], capture_output=True, timeout=5)
            subprocess.run(["sudo", "killall", "dnsmasq"], capture_output=True, timeout=5)

            # Clean up temp config files
            for conf_file in ["/tmp/hostapd_provisioning.conf", "/tmp/dnsmasq_provisioning.conf"]:
                try:
                    if os.path.exists(conf_file):
                        os.remove(conf_file)
                except Exception:
                    pass

            # Restore WiFi interface to managed mode (not AP mode)
            # Use configured interface from wifi_monitor, fallback to wlan0
            wifi_interface = "wlan0"
            if self.wifi_monitor and hasattr(self.wifi_monitor, 'cfg'):
                wifi_interface = getattr(self.wifi_monitor.cfg, 'interface', 'wlan0')
            
            subprocess.run(
                ["sudo", "ip", "addr", "flush", "dev", wifi_interface],
                capture_output=True, timeout=5
            )

            logger.info("WiFi hotspot stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Error stopping WiFi hotspot: {e}")
            return False

    def _start_wifi_hotspot(self) -> bool:
        """Start WiFi hotspot for provisioning (only when LTE is disabled)"""
        try:
            logger.info("Starting WiFi hotspot for provisioning...")

            # Start the provisioning service
            result = subprocess.run(
                ["sudo", "systemctl", "start", "tsv6-wifi-provisioning.service"],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                logger.info("WiFi hotspot provisioning service started")
                return True
            else:
                logger.error(f"Failed to start WiFi hotspot: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"Error starting WiFi hotspot: {e}")
            return False

    def start(self) -> None:
        """Start connectivity management"""
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()

        # If LTE is primary or LTE-only mode, stop WiFi hotspot immediately
        # Hotspot should only run when LTE is disabled
        if self._primary == ConnectionType.LTE or self.config.mode == ConnectivityMode.LTE_ONLY:
            if self._is_wifi_hotspot_active():
                logger.info("LTE mode active: stopping WiFi hotspot at startup")
                self._stop_wifi_hotspot()

        # If LTE is primary and WiFi is backup, notify network monitor immediately
        # This prevents WiFi provisioning from being triggered during LTE startup wait
        if (self._primary == ConnectionType.LTE and
            self._backup == ConnectionType.WIFI and
            self.wifi_monitor and
            hasattr(self.wifi_monitor, 'set_wifi_intentionally_disabled')):
            logger.info("LTE-first mode: marking WiFi as intentionally disabled at startup")
            self.wifi_monitor.set_wifi_intentionally_disabled(True)

        # Register callbacks with monitors
        if self.wifi_monitor:
            # Store original callbacks to chain them
            self._orig_wifi_status = self.wifi_monitor.on_status
            self._orig_wifi_disconnect = self.wifi_monitor.on_disconnect
            self._orig_wifi_reconnect = self.wifi_monitor.on_reconnect

            self.wifi_monitor.on_status = self._on_wifi_status
            self.wifi_monitor.on_disconnect = self._on_wifi_disconnect
            self.wifi_monitor.on_reconnect = self._on_wifi_reconnect

        if self.lte_monitor:
            self._orig_lte_status = self.lte_monitor.on_status
            self._orig_lte_disconnect = self.lte_monitor.on_disconnect
            self._orig_lte_reconnect = self.lte_monitor.on_reconnect

            self.lte_monitor.on_status = self._on_lte_status
            self.lte_monitor.on_disconnect = self._on_lte_disconnect
            self.lte_monitor.on_reconnect = self._on_lte_reconnect

        # Start management thread
        self._thread = threading.Thread(
            target=self._management_loop,
            name="ConnectivityManager",
            daemon=True
        )
        self._thread.start()

        logger.info("Connectivity manager started")

    def stop(self) -> None:
        """Stop connectivity management"""
        logger.info("Stopping connectivity manager...")
        self._stop.set()

        if self._thread:
            self._thread.join(timeout=5)

        # Restore original callbacks
        if self.wifi_monitor and hasattr(self, '_orig_wifi_status'):
            self.wifi_monitor.on_status = self._orig_wifi_status
            self.wifi_monitor.on_disconnect = self._orig_wifi_disconnect
            self.wifi_monitor.on_reconnect = self._orig_wifi_reconnect

        if self.lte_monitor and hasattr(self, '_orig_lte_status'):
            self.lte_monitor.on_status = self._orig_lte_status
            self.lte_monitor.on_disconnect = self._orig_lte_disconnect
            self.lte_monitor.on_reconnect = self._orig_lte_reconnect

        logger.info("Connectivity manager stopped")

    def _on_wifi_status(self, status: Dict[str, Any]) -> None:
        """Handle WiFi status update"""
        self._wifi_status = status
        # FIX: NetworkMonitor emits 'wifi_ok' and 'connectivity_ok' keys, not 'connected'.
        # Use the correct keys to determine WiFi connectivity.
        # Priority: wifi_ok (indicates WiFi link is up) > connectivity_ok (indicates internet) > connected (legacy)
        if 'wifi_ok' in status:
            # Modern NetworkMonitor: wifi_ok indicates WiFi association
            self._wifi_connected = status.get('wifi_ok', False)
        elif 'connectivity_ok' in status:
            # Alternative: connectivity_ok indicates network connectivity
            self._wifi_connected = status.get('connectivity_ok', False)
        else:
            # Legacy fallback
            self._wifi_connected = status.get('connected', False)

        # Chain to original callback
        if hasattr(self, '_orig_wifi_status') and self._orig_wifi_status:
            try:
                self._orig_wifi_status(status)
            except Exception as e:
                logger.error(f"Original WiFi status callback error: {e}")

    def _on_wifi_disconnect(self, status: Dict[str, Any]) -> None:
        """Handle WiFi disconnect"""
        self._wifi_connected = False
        logger.info("WiFi disconnected")

        # Check if we need to failover
        if self._active_connection == ConnectionType.WIFI:
            self._handle_primary_failure()

        # Chain to original callback
        if hasattr(self, '_orig_wifi_disconnect') and self._orig_wifi_disconnect:
            try:
                self._orig_wifi_disconnect(status)
            except Exception as e:
                logger.error(f"Original WiFi disconnect callback error: {e}")

    def _on_wifi_reconnect(self, status: Dict[str, Any]) -> None:
        """Handle WiFi reconnect"""
        self._wifi_connected = True
        logger.info("WiFi reconnected")

        # Check if we should failback
        self._check_failback()

        # Chain to original callback
        if hasattr(self, '_orig_wifi_reconnect') and self._orig_wifi_reconnect:
            try:
                self._orig_wifi_reconnect(status)
            except Exception as e:
                logger.error(f"Original WiFi reconnect callback error: {e}")

    def _on_lte_status(self, status: Dict[str, Any]) -> None:
        """Handle LTE status update"""
        self._lte_status = status
        self._lte_connected = status.get('connected', False) and status.get('ping_success', False)

        # Chain to original callback
        if hasattr(self, '_orig_lte_status') and self._orig_lte_status:
            try:
                self._orig_lte_status(status)
            except Exception as e:
                logger.error(f"Original LTE status callback error: {e}")

    def _on_lte_disconnect(self, status: Dict[str, Any]) -> None:
        """Handle LTE disconnect"""
        self._lte_connected = False
        logger.info("LTE disconnected")

        # Check if we need to failover
        if self._active_connection == ConnectionType.LTE:
            self._handle_primary_failure()

        # Chain to original callback
        if hasattr(self, '_orig_lte_disconnect') and self._orig_lte_disconnect:
            try:
                self._orig_lte_disconnect(status)
            except Exception as e:
                logger.error(f"Original LTE disconnect callback error: {e}")

    def _on_lte_reconnect(self, status: Dict[str, Any]) -> None:
        """Handle LTE reconnect"""
        self._lte_connected = True
        logger.info("LTE reconnected")

        # Check if we should failback
        self._check_failback()

        # Chain to original callback
        if hasattr(self, '_orig_lte_reconnect') and self._orig_lte_reconnect:
            try:
                self._orig_lte_reconnect(status)
            except Exception as e:
                logger.error(f"Original LTE reconnect callback error: {e}")

    def _is_connection_available(self, conn_type: ConnectionType) -> bool:
        """Check if a connection type is available"""
        if conn_type == ConnectionType.WIFI:
            return self._wifi_connected and self.wifi_monitor is not None
        elif conn_type == ConnectionType.LTE:
            return self._lte_connected and self.lte_monitor is not None
        return False

    def _handle_primary_failure(self) -> None:
        """Handle failure of primary connection"""
        with self._lock:
            if self._failover_in_progress:
                return

            if self._primary_failure_start == 0:
                self._primary_failure_start = time.time()
                logger.info(f"Primary connection ({self._primary.value}) failed, starting failover timer")

    def _check_failback(self) -> None:
        """Check if we should failback to primary connection"""
        with self._lock:
            # Only failback if we're on backup
            if self._active_connection == self._primary:
                return

            # Check if primary is available
            if self._is_connection_available(self._primary):
                # Reset failure timer
                self._primary_failure_start = 0
                logger.info(f"Primary connection ({self._primary.value}) recovered, will failback after stability check")

    def _set_active_connection(self, new_connection: ConnectionType) -> None:
        """Set the active connection and trigger callback"""
        if new_connection != self._active_connection:
            old_connection = self._active_connection
            self._active_connection = new_connection
            logger.info(f"Active connection changed: {old_connection.value} -> {new_connection.value}")

            if self.on_connection_change:
                try:
                    self.on_connection_change(old_connection, new_connection)
                except Exception as e:
                    logger.error(f"Connection change callback error: {e}")

    def _management_loop(self) -> None:
        """Main management loop"""
        logger.info("Connectivity management loop starting")

        last_status_report = 0

        # LTE-first startup: if LTE is primary, disable WiFi immediately and wait for LTE
        if (self._primary == ConnectionType.LTE and
            self._backup == ConnectionType.WIFI):
            logger.info("LTE-first startup: disabling WiFi and hotspot to prioritize LTE connection")
            self._log_network_snapshot("lte_first_startup_entry")

            # Stop WiFi hotspot if running (hotspot should only run when LTE is disabled)
            if self._is_wifi_hotspot_active():
                self._stop_wifi_hotspot()

            # Disable WiFi client connection
            if self._is_wifi_active():
                self._disable_wifi()

            # Signal LTE wait start (for splash screen)
            if self.on_lte_wait_start:
                try:
                    self.on_lte_wait_start(
                        self.config.lte_splash_image,
                        self.config.lte_splash_text
                    )
                except Exception as e:
                    logger.warning(f"LTE wait start callback error: {e}")

            # Wait for LTE to connect with timeout
            lte_startup_deadline = time.time() + self.config.lte_startup_wait_secs
            lte_connected = False

            logger.info(f"Waiting up to {self.config.lte_startup_wait_secs}s for LTE to connect...")
            while time.time() < lte_startup_deadline and not self._stop.is_set():
                if self._is_connection_available(ConnectionType.LTE):
                    lte_connected = True
                    logger.info("LTE connected successfully - WiFi will remain disabled for power saving")
                    self._set_active_connection(ConnectionType.LTE)
                    break
                time.sleep(5)

            # Signal LTE wait end (to hide splash screen)
            if self.on_lte_wait_end:
                try:
                    self.on_lte_wait_end(lte_connected)
                except Exception as e:
                    logger.warning(f"LTE wait end callback error: {e}")

            if not lte_connected and not self._stop.is_set():
                logger.warning(f"LTE did not connect within {self.config.lte_startup_wait_secs}s - enabling WiFi fallback")
                self._enable_wifi()
                time.sleep(10)  # Wait for WiFi to connect
                if self._is_connection_available(ConnectionType.WIFI):
                    logger.info("WiFi fallback connected")
                    self._set_active_connection(ConnectionType.WIFI)
        else:
            # Non-LTE primary mode: normal startup delay
            time.sleep(5)

        while not self._stop.is_set():
            try:
                with self._lock:
                    # Determine best available connection
                    primary_available = self._is_connection_available(self._primary)

                    # For backup availability, check if WiFi is actually active
                    # (it may be disabled by us for power saving)
                    if self._backup == ConnectionType.WIFI and self._wifi_disabled_by_us:
                        backup_available = False  # WiFi is disabled, not available
                    else:
                        backup_available = self._backup and self._is_connection_available(self._backup)

                    current_time = time.time()

                    # Handle failover logic
                    if self._active_connection == ConnectionType.NONE:
                        # No active connection, try to establish one
                        if primary_available:
                            self._set_active_connection(self._primary)
                            self._primary_failure_start = 0
                            # Disable backup for power saving
                            if self.config.disable_backup_when_primary_active and self._backup == ConnectionType.WIFI:
                                # Stop hotspot if running (should only run when LTE is disabled)
                                if self._is_wifi_hotspot_active():
                                    logger.info("Power saving: stopping WiFi hotspot (LTE connected)")
                                    self._stop_wifi_hotspot()
                                # Disable WiFi client
                                if self._is_wifi_active():
                                    logger.info("Power saving: disabling WiFi (LTE connected)")
                                    self._disable_wifi()
                        elif self._backup == ConnectionType.WIFI:
                            # Primary not available, enable WiFi for failover
                            if self._wifi_disabled_by_us:
                                logger.info("Enabling WiFi for failover (LTE unavailable)")
                                self._enable_wifi()
                                time.sleep(5)  # Wait for WiFi to connect
                            if self._is_connection_available(self._backup):
                                self._set_active_connection(self._backup)

                    elif self._active_connection == self._primary:
                        # On primary connection
                        if not primary_available:
                            # Primary failed
                            if self._primary_failure_start == 0:
                                self._primary_failure_start = current_time
                                logger.warning(f"Primary ({self._primary.value}) connection lost")

                            # Check if failover timeout reached
                            elapsed = current_time - self._primary_failure_start
                            if elapsed >= self.config.failover_timeout_secs:
                                # Enable WiFi for failover
                                if self._backup == ConnectionType.WIFI and self._wifi_disabled_by_us:
                                    logger.info("Enabling WiFi for failover")
                                    self._enable_wifi()
                                    time.sleep(10)  # Wait for WiFi to connect

                                if self._is_connection_available(self._backup):
                                    logger.warning(f"Failing over from {self._primary.value} to {self._backup.value}")
                                    self._set_active_connection(self._backup)
                                    self._last_failover_time = current_time
                                else:
                                    logger.error("Failover failed: backup not available")
                        else:
                            # Primary is working
                            if self._primary_failure_start != 0:
                                logger.info(f"Primary ({self._primary.value}) recovered")
                            self._primary_failure_start = 0

                            # CONTINUOUS ENFORCEMENT: Ensure WiFi stays completely disabled when LTE is active
                            # This catches cases where WiFi might have been re-enabled by other processes
                            if self.config.disable_backup_when_primary_active and self._backup == ConnectionType.WIFI:
                                # Check if WiFi radio is somehow enabled - more aggressive than just checking connection
                                if self._is_wifi_radio_enabled():
                                    logger.warning("WiFi radio found enabled while LTE is active - disabling radio")
                                    self._disable_wifi()
                                # Also ensure the intentionally_disabled flag stays set on NetworkMonitor
                                if self.wifi_monitor and hasattr(self.wifi_monitor, 'is_wifi_intentionally_disabled'):
                                    if not self.wifi_monitor.is_wifi_intentionally_disabled():
                                        logger.warning("WiFi intentionally_disabled flag was reset - resetting to True")
                                        self.wifi_monitor.set_wifi_intentionally_disabled(True)

                    elif self._active_connection == self._backup:
                        # On backup connection (WiFi failover)
                        if not self._is_connection_available(self._backup):
                            # Backup also failed
                            if primary_available:
                                self._set_active_connection(self._primary)
                                # Disable WiFi for power saving
                                if self.config.disable_backup_when_primary_active and self._backup == ConnectionType.WIFI:
                                    self._disable_wifi()
                            else:
                                self._set_active_connection(ConnectionType.NONE)

                        elif primary_available:
                            # Primary recovered, check stability before failback
                            if self._primary_failure_start == 0:
                                self._primary_failure_start = current_time
                                logger.info(f"Primary ({self._primary.value}) available, waiting for stability...")

                            stability_time = current_time - self._primary_failure_start
                            if stability_time >= self.config.failback_stability_secs:
                                logger.info(f"Failing back to primary ({self._primary.value})")
                                self._set_active_connection(self._primary)
                                self._primary_failure_start = 0
                                # Disable WiFi for power saving
                                if self.config.disable_backup_when_primary_active and self._backup == ConnectionType.WIFI:
                                    logger.info("Power saving: disabling WiFi after failback")
                                    self._disable_wifi()

                # Periodic status report
                if current_time - last_status_report >= self.config.status_report_interval_secs:
                    self._report_status()
                    last_status_report = current_time

            except Exception as e:
                logger.error(f"Connectivity management error: {e}")

            # Check interval
            self._stop.wait(5.0)

        logger.info("Connectivity management loop exited")

    def _report_status(self) -> None:
        """Report current connectivity status"""
        if self.on_status:
            status = self.get_status()
            try:
                self.on_status(status)
            except Exception as e:
                logger.error(f"Status report callback error: {e}")

    def get_active_connection(self) -> ConnectionType:
        """Get currently active connection type"""
        return self._active_connection

    def get_active_connection_str(self) -> str:
        """Get currently active connection type as string"""
        return self._active_connection.value

    def is_connected(self) -> bool:
        """Check if any connection is active"""
        return self._active_connection != ConnectionType.NONE

    def is_wifi_connected(self) -> bool:
        """Check if WiFi is connected (may not be active)"""
        return self._wifi_connected

    def is_lte_connected(self) -> bool:
        """Check if LTE is connected (may not be active)"""
        return self._lte_connected

    def is_metered(self) -> bool:
        """Check if current connection is metered (LTE)"""
        return self._active_connection == ConnectionType.LTE

    def force_connection(self, conn_type: ConnectionType) -> bool:
        """
        Force switch to a specific connection type.

        Args:
            conn_type: Connection type to switch to

        Returns:
            True if switch successful
        """
        with self._lock:
            if not self._is_connection_available(conn_type):
                logger.warning(f"Cannot force connection to {conn_type.value} - not available")
                return False

            logger.info(f"Forcing connection to {conn_type.value}")
            self._set_active_connection(conn_type)
            return True

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive connectivity status"""
        return {
            'mode': self.config.mode.value,
            'active_connection': self._active_connection.value,
            'is_connected': self.is_connected(),
            'is_metered': self.is_metered(),
            'wifi': {
                'connected': self._wifi_connected,
                'available': self.wifi_monitor is not None,
                'details': self._wifi_status,
            },
            'lte': {
                'connected': self._lte_connected,
                'available': self.lte_monitor is not None,
                'details': self._lte_status,
            },
            'primary': self._primary.value,
            'backup': self._backup.value if self._backup else None,
            'failover_in_progress': self._failover_in_progress,
            'last_failover_time': self._last_failover_time,
        }
