#!/usr/bin/env python3
"""
Connection Duration Tracker and Deadline Monitor

Tracks AWS IoT connection state, duration, and enforces connection deadlines
to prevent indefinite disconnection loops. Implements forced reboot policy
for production IoT device reliability.

Key Features:
- Connection state tracking (connected/disconnected duration)
- Connection deadline enforcement (30-minute forced reboot)
- Reconnection attempt tracking
- Connection quality metrics
- Integration with error recovery system

Issue: #TS_538A7DD4 - Production device stuck in reconnection loops
"""

import time
import threading
import logging
import subprocess
from typing import Optional, Callable
from dataclasses import dataclass
from enum import Enum


class ConnectionState(Enum):
    """Connection state enumeration"""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    RECONNECTING = "reconnecting"


@dataclass
class ConnectionMetrics:
    """Connection quality and duration metrics"""
    state: ConnectionState
    connected_since: Optional[float]
    disconnected_since: Optional[float]
    total_connected_time: float
    total_disconnected_time: float
    reconnection_attempts: int
    successful_reconnections: int
    failed_reconnections: int
    current_uptime_minutes: float
    current_downtime_minutes: float
    uptime_percentage_24h: float
    last_state_change: float


class ConnectionDeadlineMonitor:
    """
    Monitor connection duration and enforce deadline for forced recovery.
    
    Prevents production IoT devices from being stuck in indefinite
    reconnection loops by forcing a system reboot after a configurable
    deadline (default: 30 minutes).
    """
    
    def __init__(
        self,
        disconnection_deadline_minutes: int = 30,
        check_interval_seconds: int = 60,
        on_deadline_exceeded: Optional[Callable] = None,
        enable_forced_reboot: bool = True,
        systemd_recovery_manager: Optional['SystemdRecoveryManager'] = None
    ):
        """
        Initialize connection deadline monitor.
        
        Args:
            disconnection_deadline_minutes: Max minutes disconnected before forced reboot
            check_interval_seconds: How often to check deadline
            on_deadline_exceeded: Callback when deadline exceeded (before reboot)
            enable_forced_reboot: Enable automatic reboot (set False for testing)
            systemd_recovery_manager: SystemD recovery manager for privileged operations
        """
        self.deadline_minutes = disconnection_deadline_minutes
        self.check_interval = check_interval_seconds
        self.on_deadline_exceeded = on_deadline_exceeded
        self.enable_forced_reboot = enable_forced_reboot
        self.systemd_recovery = systemd_recovery_manager
        
        self.logger = logging.getLogger(__name__)
        self.running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()  # CRITICAL FIX: Thread safety
        
        # State tracking
        self.disconnected_since: Optional[float] = None
        self.deadline_exceeded = False
        
        self.logger.info(
            f"AWS IoT Connection Deadline Monitor initialized: "
            f"deadline={self.deadline_minutes} min, "
            f"forced_reboot={'enabled' if self.enable_forced_reboot else 'disabled'}"
        )
    
    def start(self):
        """Start deadline monitoring in background thread"""
        if self.running:
            return
        
        self.running = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="ConnectionDeadlineMonitor",
            daemon=True
        )
        self._monitor_thread.start()
        self.logger.info("Connection deadline monitoring started")
    
    def stop(self):
        """Stop deadline monitoring"""
        if not self.running:
            return
        
        self.logger.info("Stopping connection deadline monitor...")
        self.running = False
        self._stop_event.set()
        
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        
        self.logger.info("Connection deadline monitor stopped")
    
    def mark_disconnected(self):
        """Mark connection as disconnected - starts deadline timer"""
        with self._state_lock:  # CRITICAL FIX: Thread safety
            if self.disconnected_since is None:
                self.disconnected_since = time.time()
                self.deadline_exceeded = False
                self.logger.warning(
                    f"AWS IoT connection marked as DISCONNECTED - deadline timer started "
                    f"({self.deadline_minutes} min)"
                )
    
    def mark_connected(self):
        """Mark connection as connected - resets deadline timer"""
        with self._state_lock:  # CRITICAL FIX: Thread safety
            if self.disconnected_since is not None:
                downtime_minutes = (time.time() - self.disconnected_since) / 60
                self.logger.info(
                    f"AWS IoT connection marked as CONNECTED - deadline timer reset "
                    f"(was disconnected for {downtime_minutes:.1f} min)"
                )
                self.disconnected_since = None
                self.deadline_exceeded = False
    
    def get_disconnection_duration_minutes(self) -> float:
        """Get current disconnection duration in minutes"""
        if self.disconnected_since is None:
            return 0.0
        return (time.time() - self.disconnected_since) / 60
    
    def is_deadline_exceeded(self) -> bool:
        """Check if disconnection deadline has been exceeded"""
        if self.disconnected_since is None:
            return False
        
        downtime_minutes = self.get_disconnection_duration_minutes()
        return downtime_minutes >= self.deadline_minutes
    
    def _monitor_loop(self):
        """Main monitoring loop - checks deadline periodically"""
        while not self._stop_event.wait(self.check_interval):
            try:
                if self.is_deadline_exceeded() and not self.deadline_exceeded:
                    self._handle_deadline_exceeded()
            except Exception as e:
                self.logger.error(f"Error in deadline monitor loop: {e}")
    
    def _handle_deadline_exceeded(self):
        """Handle deadline exceeded - notify and force reboot"""
        downtime_minutes = self.get_disconnection_duration_minutes()
        
        self.logger.critical(
            f"🚨 AWS IOT CONNECTION DEADLINE EXCEEDED! "
            f"AWS IoT disconnected for {downtime_minutes:.1f} minutes "
            f"(deadline: {self.deadline_minutes} min)"
        )
        
        self.deadline_exceeded = True
        
        # Notify callback if provided
        if self.on_deadline_exceeded:
            try:
                self.on_deadline_exceeded(downtime_minutes)
            except Exception as e:
                self.logger.error(f"Error in deadline exceeded callback: {e}")
        
        # Execute forced reboot if enabled
        if self.enable_forced_reboot:
            self._execute_forced_reboot()
        else:
            self.logger.warning(
                "Forced reboot DISABLED - would reboot in production"
            )
    
    def _execute_forced_reboot(self):
        """
        Execute forced system reboot using systemd (CRITICAL FIX).
        
        Uses SystemdRecoveryManager for proper systemd-based reboot
        instead of 'sudo reboot' which requires passwordless sudo.
        """
        self.logger.critical(
            "🚨 FORCED SYSTEM REBOOT - AWS IoT connection deadline exceeded"
        )
        
        try:
            # Sync filesystem before reboot to prevent data corruption
            subprocess.run(['sync'], timeout=10)
            time.sleep(2)
            
            # CRITICAL FIX: Use systemd-based reboot instead of sudo
            if self.systemd_recovery and hasattr(self.systemd_recovery, 'execute_system_reboot'):
                self.logger.info("Using SystemdRecoveryManager for reboot...")
                success = self.systemd_recovery.execute_system_reboot()
                
                if success:
                    self.logger.critical("System reboot initiated via systemd ✅")
                    return
                else:
                    self.logger.error("SystemdRecoveryManager reboot failed, trying fallback")
            else:
                self.logger.warning("SystemdRecoveryManager not available, using fallback")
            
            # Fallback 1: Try systemctl reboot (no sudo required if polkit configured)
            try:
                self.logger.info("Fallback: Attempting systemctl reboot...")
                subprocess.run(['systemctl', 'reboot'], timeout=5, check=False)
                self.logger.critical("System reboot via systemctl initiated")
                return
            except Exception as systemctl_error:
                self.logger.error(f"systemctl reboot failed: {systemctl_error}")
            
            # Fallback 2: Try direct reboot command (may require permissions)
            try:
                self.logger.warning("Last resort: Attempting direct reboot command...")
                subprocess.run(['reboot'], timeout=5, check=False)
                self.logger.critical("System reboot via direct command initiated")
                return
            except Exception as reboot_error:
                self.logger.error(f"Direct reboot failed: {reboot_error}")
            
            # All methods failed - CRITICAL ERROR
            self.logger.critical(
                "❌ CRITICAL: All reboot methods failed! Manual intervention required."
            )
            self.logger.critical(
                "Device may remain disconnected indefinitely without reboot."
            )
            
        except Exception as e:
            self.logger.critical(f"Failed to execute forced reboot: {e}")
            self.logger.critical("Manual intervention required to recover device.")


class ConnectionTracker:
    """
    Track connection state, duration, and quality metrics.
    
    Provides visibility into connection health and enables timeout-based
    recovery decisions.
    """
    
    def __init__(self):
        """Initialize connection tracker"""
        self.logger = logging.getLogger(__name__)
        
        # CRITICAL FIX: Thread safety
        self._state_lock = threading.Lock()
        
        # State tracking
        self.current_state = ConnectionState.DISCONNECTED
        self.connected_since: Optional[float] = None
        self.disconnected_since: Optional[float] = time.time()
        self.last_state_change = time.time()
        
        # Cumulative metrics
        self.total_connected_time = 0.0
        self.total_disconnected_time = 0.0
        self.reconnection_attempts = 0
        self.successful_reconnections = 0
        self.failed_reconnections = 0
        
        # 24-hour rolling window for uptime calculation
        self._state_history = []  # List of (timestamp, state) tuples
        self._history_window_seconds = 86400  # 24 hours
        
        self.logger.info("Connection tracker initialized")
    
    def mark_connected(self):
        """Mark connection as connected"""
        with self._state_lock:  # CRITICAL FIX: Thread safety
            now = time.time()
            
            # Update cumulative downtime if was disconnected
            if self.current_state in [ConnectionState.DISCONNECTED, ConnectionState.RECONNECTING]:
                if self.disconnected_since:
                    downtime = now - self.disconnected_since
                    self.total_disconnected_time += downtime
                    self.successful_reconnections += 1
                    
                    self.logger.info(
                        f"Connection established (downtime: {downtime/60:.1f} min, "
                        f"attempts: {self.reconnection_attempts})"
                    )
            
            # Update state
            self.current_state = ConnectionState.CONNECTED
            self.connected_since = now
            self.disconnected_since = None
            self.last_state_change = now
            self.reconnection_attempts = 0
            
            # Add to history
            self._add_to_history(now, ConnectionState.CONNECTED)
    
    def mark_disconnected(self):
        """Mark connection as disconnected"""
        with self._state_lock:  # CRITICAL FIX: Thread safety
            now = time.time()
            
            # Update cumulative uptime if was connected
            if self.current_state == ConnectionState.CONNECTED:
                if self.connected_since:
                    uptime = now - self.connected_since
                    self.total_connected_time += uptime
                    
                    self.logger.warning(
                        f"Connection lost (uptime: {uptime/60:.1f} min)"
                    )
            
            # Update state
            self.current_state = ConnectionState.DISCONNECTED
            self.disconnected_since = now
            self.connected_since = None
            self.last_state_change = now
            
            # Add to history
            self._add_to_history(now, ConnectionState.DISCONNECTED)
    
    def mark_reconnecting(self):
        """Mark connection as attempting reconnection"""
        with self._state_lock:  # CRITICAL FIX: Thread safety
            self.reconnection_attempts += 1
            
            if self.current_state != ConnectionState.RECONNECTING:
                self.logger.info(f"Reconnection attempt #{self.reconnection_attempts}")
                self.current_state = ConnectionState.RECONNECTING
                self.last_state_change = time.time()
    
    def mark_reconnection_failed(self):
        """Mark reconnection attempt as failed"""
        with self._state_lock:  # CRITICAL FIX: Thread safety
            self.failed_reconnections += 1
            self.logger.warning(
                f"Reconnection attempt #{self.reconnection_attempts} failed "
                f"(total failures: {self.failed_reconnections})"
            )
    
    def get_current_uptime_minutes(self) -> float:
        """Get current continuous connection uptime in minutes"""
        if self.current_state == ConnectionState.CONNECTED and self.connected_since:
            return (time.time() - self.connected_since) / 60
        return 0.0
    
    def get_current_downtime_minutes(self) -> float:
        """Get current continuous disconnection downtime in minutes"""
        if self.current_state in [ConnectionState.DISCONNECTED, ConnectionState.RECONNECTING]:
            if self.disconnected_since:
                return (time.time() - self.disconnected_since) / 60
        return 0.0
    
    def get_uptime_percentage_24h(self) -> float:
        """Calculate uptime percentage over last 24 hours"""
        now = time.time()
        cutoff = now - self._history_window_seconds
        
        # Clean old history
        self._state_history = [
            (ts, state) for ts, state in self._state_history
            if ts >= cutoff
        ]
        
        if not self._state_history:
            # No history, use current state
            if self.current_state == ConnectionState.CONNECTED:
                return 100.0
            return 0.0
        
        # Calculate uptime from state transitions
        connected_time = 0.0
        last_ts = max(cutoff, self._state_history[0][0])
        last_state = self._state_history[0][1]
        
        for ts, state in self._state_history[1:]:
            if last_state == ConnectionState.CONNECTED:
                connected_time += (ts - last_ts)
            last_ts = ts
            last_state = state
        
        # Add current state duration
        if last_state == ConnectionState.CONNECTED:
            connected_time += (now - last_ts)
        
        # Calculate percentage
        total_time = now - cutoff
        if total_time <= 0:
            return 0.0
        
        return (connected_time / total_time) * 100
    
    def get_metrics(self) -> ConnectionMetrics:
        """Get comprehensive connection metrics"""
        return ConnectionMetrics(
            state=self.current_state,
            connected_since=self.connected_since,
            disconnected_since=self.disconnected_since,
            total_connected_time=self.total_connected_time,
            total_disconnected_time=self.total_disconnected_time,
            reconnection_attempts=self.reconnection_attempts,
            successful_reconnections=self.successful_reconnections,
            failed_reconnections=self.failed_reconnections,
            current_uptime_minutes=self.get_current_uptime_minutes(),
            current_downtime_minutes=self.get_current_downtime_minutes(),
            uptime_percentage_24h=self.get_uptime_percentage_24h(),
            last_state_change=self.last_state_change
        )
    
    def _add_to_history(self, timestamp: float, state: ConnectionState):
        """Add state change to history"""
        self._state_history.append((timestamp, state))
        
        # Limit history size (keep last 1000 events)
        if len(self._state_history) > 1000:
            self._state_history = self._state_history[-1000:]
    
    def get_status_summary(self) -> dict:
        """Get human-readable status summary"""
        metrics = self.get_metrics()
        
        return {
            "state": metrics.state.value,
            "current_uptime_minutes": round(metrics.current_uptime_minutes, 1),
            "current_downtime_minutes": round(metrics.current_downtime_minutes, 1),
            "reconnection_attempts": metrics.reconnection_attempts,
            "uptime_percentage_24h": round(metrics.uptime_percentage_24h, 1),
            "successful_reconnections": metrics.successful_reconnections,
            "failed_reconnections": metrics.failed_reconnections,
            "total_connected_hours": round(metrics.total_connected_time / 3600, 1),
            "total_disconnected_hours": round(metrics.total_disconnected_time / 3600, 1)
        }
