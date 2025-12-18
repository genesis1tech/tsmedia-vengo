#!/usr/bin/env python3
"""
Connectivity Manager for TSV6 Raspberry Pi

Manages network connectivity with WiFi/LTE failover support.
Provides unified interface for AWS IoT, OTA updates, and general connectivity.

Default mode: LTE Primary + WiFi Backup
"""

import threading
import time
import logging
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
    status_report_interval_secs: float = 60.0

    # Connection priorities (higher = preferred when both available)
    wifi_priority: int = 100
    lte_priority: int = 200  # LTE preferred by default


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
        """
        self.config = config or ConnectivityManagerConfig()
        self.wifi_monitor = wifi_monitor
        self.lte_monitor = lte_monitor
        self.error_recovery = error_recovery_system
        self.on_connection_change = on_connection_change
        self.on_status = on_status

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

        logger.info(f"ConnectivityManager initialized (mode: {self.config.mode.value})")
        logger.info(f"Primary: {self._primary.value}, Backup: {self._backup.value if self._backup else 'none'}")

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

    def start(self) -> None:
        """Start connectivity management"""
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()

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

        # Initial delay
        time.sleep(5)

        last_status_report = 0

        while not self._stop.is_set():
            try:
                with self._lock:
                    # Determine best available connection
                    primary_available = self._is_connection_available(self._primary)
                    backup_available = self._backup and self._is_connection_available(self._backup)

                    current_time = time.time()

                    # Handle failover logic
                    if self._active_connection == ConnectionType.NONE:
                        # No active connection, try to establish one
                        if primary_available:
                            self._set_active_connection(self._primary)
                            self._primary_failure_start = 0
                        elif backup_available:
                            self._set_active_connection(self._backup)

                    elif self._active_connection == self._primary:
                        # On primary connection
                        if not primary_available:
                            # Primary failed
                            if self._primary_failure_start == 0:
                                self._primary_failure_start = current_time

                            # Check if failover timeout reached
                            if backup_available and (current_time - self._primary_failure_start) >= self.config.failover_timeout_secs:
                                logger.warning(f"Failing over from {self._primary.value} to {self._backup.value}")
                                self._set_active_connection(self._backup)
                                self._last_failover_time = current_time
                        else:
                            # Primary recovered
                            self._primary_failure_start = 0

                    elif self._active_connection == self._backup:
                        # On backup connection
                        if not backup_available:
                            # Backup also failed
                            if primary_available:
                                self._set_active_connection(self._primary)
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
