#!/usr/bin/env python3
"""
Enhanced Network Monitor for Raspberry Pi - Fixed for subprocess PATH issues

Key fix: Use full paths for system commands to avoid PATH issues
"""

import subprocess
import threading
import time
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .error_recovery import ErrorRecoverySystem
    from .systemd_recovery_manager import SystemdRecoveryManager


def _run(cmd: list[str], timeout: float = 5.0) -> Tuple[int, str, str]:
    """Run a command with timeout and return (returncode, stdout, stderr)"""
    try:
        # Ensure PATH includes system binaries
        import os
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:' + env.get('PATH', '')
        
        p = subprocess.run(
            cmd, 
            capture_output=True,
            text=True, 
            timeout=timeout,
            env=env
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


@dataclass
class NetworkMonitorConfig:
    """Configuration for network monitoring"""
    interface: str = "wlan0"
    check_interval_secs: float = 10.0  # OPTIMIZED: 30s → 10s for faster detection (Issue #TS_538A7DD4)
    weak_signal_threshold_dbm: int = -80
    ping_target_local: str = "8.8.8.8"  # Use public DNS; many routers block ICMP pings
    ping_target_public: str = "8.8.8.8"
    max_backoff_secs: float = 300.0
    soft_recovery_threshold: int = 2    # OPTIMIZED: 6 → 2 (20s vs 3 min to first recovery)
    intermediate_recovery_threshold: int = 4  # OPTIMIZED: 12 → 4 (40s vs 6 min)
    hard_recovery_threshold: int = 6    # OPTIMIZED: 18 → 6 (60s vs 9 min)
    critical_escalation_threshold: int = 12  # OPTIMIZED: 24 → 12 (2 min vs 12 min)
    startup_delay_secs: float = 10.0
    gateway_retry_count: int = 3


class NetworkRecoveryStage:
    """Track network recovery stage and attempt counts"""
    def __init__(self):
        self.consecutive_failures = 0
        self.soft_attempts = 0
        self.intermediate_attempts = 0
        self.hard_attempts = 0
        self.last_recovery_time = 0
        self.current_stage = "none"
    
    def reset(self):
        """Reset recovery tracking on successful connection"""
        self.consecutive_failures = 0
        self.soft_attempts = 0
        self.intermediate_attempts = 0
        self.hard_attempts = 0
        self.current_stage = "none"


class NetworkMonitor:
    """Enhanced network monitoring and recovery system for IoT devices"""
    
    def __init__(
        self,
        config: Optional[NetworkMonitorConfig] = None,
        on_status: Optional[Callable[[dict], None]] = None,
        on_disconnect: Optional[Callable[[dict], None]] = None,
        on_reconnect: Optional[Callable[[dict], None]] = None,
        error_recovery_system: Optional['ErrorRecoverySystem'] = None,
        systemd_recovery_manager: Optional['SystemdRecoveryManager'] = None,
    ) -> None:
        self.cfg = config or NetworkMonitorConfig()
        self.on_status = on_status
        self.on_disconnect = on_disconnect
        self.on_reconnect = on_reconnect
        self.error_recovery = error_recovery_system
        self.systemd_recovery = systemd_recovery_manager
        
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_connected = None
        self._backoff = 5.0
        self._recovery = NetworkRecoveryStage()
        self._gateway_last_updated = 0
        self._failed_ping_count = 0

        # Flag to indicate WiFi is intentionally disabled (e.g., for LTE-first mode)
        # When True, network monitor will NOT trigger WiFi provisioning on failures
        self._wifi_intentionally_disabled = False
        
        # Initialize systemd recovery manager if available
        if self.systemd_recovery and self.systemd_recovery.is_available():
            logger.info("Using systemd recovery manager for privileged operations")
        else:
            logger.warning("Systemd recovery manager not available, recovery may be limited")

        logger.info(f"Enhanced Network Monitor initialized for {self.cfg.interface}")
        logger.debug(f"Check interval: {self.cfg.check_interval_secs}s, Startup delay: {self.cfg.startup_delay_secs}s, Gateway auto-detection: enabled")

    def set_wifi_intentionally_disabled(self, disabled: bool) -> None:
        """
        Set whether WiFi is intentionally disabled (e.g., for LTE-first mode).

        When True, the network monitor will NOT trigger WiFi provisioning on failures
        because WiFi is expected to be down (LTE is being used instead).

        Args:
            disabled: True if WiFi is intentionally disabled, False otherwise
        """
        self._wifi_intentionally_disabled = disabled
        if disabled:
            logger.info("WiFi marked as intentionally disabled - provisioning will be skipped")
            # Reset failure count since WiFi being down is expected
            self._recovery.reset()
        else:
            logger.info("WiFi marked as active - normal provisioning behavior restored")

    def is_wifi_intentionally_disabled(self) -> bool:
        """Check if WiFi is intentionally disabled."""
        return self._wifi_intentionally_disabled

    def start(self) -> None:
        """Start network monitoring in background thread"""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="NetworkMonitor", daemon=True)
        self._thread.start()
        logger.info("Network monitoring started")

    def stop(self) -> None:
        """Stop network monitoring"""
        logger.info("Stopping network monitor...")
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("Network monitor stopped")

    def _get_ssid(self) -> str:
        """Get current WiFi SSID - uses full path"""
        # Try /usr/sbin/iwgetid first (most common location)
        rc, out, err = _run(["/usr/sbin/iwgetid", "-r"])
        if rc == 0 and out:
            return out
        
        # Fallback to just iwgetid (relies on PATH)
        rc, out, err = _run(["iwgetid", "-r"])
        if rc == 0 and out:
            return out

        # Debug output if both fail
        if rc != 0:
            logger.warning(f"iwgetid failed: rc={rc}, err='{err}'")

        return ""

    def _get_rssi(self) -> Optional[int]:
        """Get WiFi signal strength in dBm"""
        rc, out, _ = _run(["/usr/sbin/iwconfig", self.cfg.interface])
        if rc != 0:
            # Fallback
            rc, out, _ = _run(["iwconfig", self.cfg.interface])
        
        if rc != 0:
            return None
        
        for line in out.splitlines():
            if "Signal level=" in line:
                try:
                    part = line.split("Signal level=")[1].split()[0]
                    return int(part)
                except Exception:
                    return None
        return None

    def _ping(self, host: str, count: int = 1, timeout: int = 2) -> bool:
        """Ping a host to test connectivity"""
        rc, _, _ = _run(["/bin/ping", "-c", str(count), "-W", str(timeout), host], timeout=timeout + 2)
        if rc != 0:
            # Fallback
            rc, _, _ = _run(["ping", "-c", str(count), "-W", str(timeout), host], timeout=timeout + 2)
        return rc == 0

    def _get_gateway(self, retry: bool = True) -> str:
        """Try to determine default gateway with retry logic"""
        attempts = self.cfg.gateway_retry_count if retry else 1
        
        for attempt in range(attempts):
            rc, out, _ = _run(["/usr/sbin/ip", "route", "show", "default"])
            if rc != 0:
                rc, out, _ = _run(["ip", "route", "show", "default"])
            
            if rc == 0:
                for line in out.splitlines():
                    if "default via" in line:
                        try:
                            gateway = line.split("via")[1].split()[0]
                            if gateway and gateway != self.cfg.ping_target_local:
                                logger.info(f"Gateway detected: {gateway} (attempt {attempt + 1}/{attempts})")
                            return gateway
                        except:
                            pass

            if attempt < attempts - 1:
                time.sleep(2)

        logger.warning(f"Using default gateway: {self.cfg.ping_target_local}")
        return self.cfg.ping_target_local

    def _update_gateway_if_needed(self, force: bool = False) -> None:
        """Update gateway address if needed"""
        current_time = time.time()
        
        should_update = (
            force or 
            self._gateway_last_updated == 0 or
            (self._failed_ping_count >= 2 and current_time - self._gateway_last_updated > 60)
        )
        
        if should_update:
            logger.info("Re-detecting gateway...")
            old_gateway = self.cfg.ping_target_local
            new_gateway = self._get_gateway(retry=True)

            if new_gateway != old_gateway:
                logger.info(f"Gateway updated: {old_gateway} → {new_gateway}")
                self.cfg.ping_target_local = new_gateway
                self._failed_ping_count = 0
            else:
                logger.debug(f"Gateway unchanged: {new_gateway}")

            self._gateway_last_updated = current_time

    def _soft_recovery(self) -> bool:
        """Perform soft recovery: WPA reconfigure + DHCP refresh"""
        try:
            logger.info("Performing soft network recovery (WPA reconfigure + DHCP)...")

            # Use systemd recovery manager if available
            if self.systemd_recovery and self.systemd_recovery.is_available():
                return self.systemd_recovery.execute_soft_recovery()

            # Fallback to direct commands (will likely fail without sudo)
            logger.warning("Systemd recovery not available, attempting direct commands...")
            _run(["wpa_cli", "-i", self.cfg.interface, "reconfigure"], timeout=10)
            time.sleep(3)

            # These will likely fail without proper sudo configuration
            _run(["dhclient", "-r", self.cfg.interface], timeout=5)
            time.sleep(2)
            _run(["dhclient", self.cfg.interface], timeout=15)
            time.sleep(5)

            self._update_gateway_if_needed(force=True)

            logger.info("Soft network recovery completed (may be incomplete)")
            return True

        except Exception as e:
            logger.error(f"Soft recovery failed: {e}")
            return False

    def _intermediate_recovery(self) -> bool:
        """Perform intermediate recovery: WiFi driver reload + network service restart"""
        try:
            logger.info("Performing intermediate network recovery (driver reload + service restart)...")

            # Use systemd recovery manager if available
            if self.systemd_recovery and self.systemd_recovery.is_available():
                return self.systemd_recovery.execute_intermediate_recovery()

            # Fallback to error recovery system
            if self.error_recovery:
                if self.error_recovery.reload_wifi_driver():
                    time.sleep(5)
                    self._update_gateway_if_needed(force=True)
                    logger.info("Intermediate network recovery (via error recovery) completed")
                    return True

            # Final fallback to direct commands (will likely fail without sudo)
            logger.warning("Systemd recovery not available, attempting direct commands...")
            result = subprocess.run(['lsmod'], capture_output=True, text=True)
            wifi_modules = []

            common_modules = ['brcmfmac', 'brcmutil', 'cfg80211']
            for line in result.stdout.splitlines():
                for module in common_modules:
                    if line.startswith(module):
                        wifi_modules.append(module)

            if not wifi_modules:
                wifi_modules = ['brcmfmac', 'brcmutil']

            for module in reversed(wifi_modules):
                _run(['modprobe', '-r', module], timeout=10)

            time.sleep(3)

            for module in wifi_modules:
                _run(['modprobe', module], timeout=10)

            time.sleep(5)

            _run(['systemctl', 'restart', 'networking'], timeout=30)
            time.sleep(5)

            self._update_gateway_if_needed(force=True)

            logger.info("Intermediate network recovery completed (may be incomplete)")
            return True

        except Exception as e:
            logger.error(f"Intermediate recovery failed: {e}")
            return False

    def _trigger_wifi_provisioning(self):
        """Start WiFi provisioning service when recovery is exhausted"""
        try:
            logger.info("Starting WiFi provisioning service...")
            result = subprocess.run(
                ['sudo', 'systemctl', 'start', 'tsv6-wifi-provisioning.service'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                logger.info("WiFi provisioning service started successfully")
            else:
                logger.error(f"Failed to start WiFi provisioning: {result.stderr}")
        except subprocess.TimeoutExpired:
            logger.error("Timeout starting WiFi provisioning service")
        except Exception as e:
            logger.error(f"Error triggering WiFi provisioning: {e}")

    def _hard_recovery(self) -> bool:
        """Perform hard recovery: Interface down/up + full networking restart"""
        try:
            logger.info("Performing hard network recovery (interface restart + full networking)...")

            # Use systemd recovery manager if available
            if self.systemd_recovery and self.systemd_recovery.is_available():
                return self.systemd_recovery.execute_hard_recovery()

            # Fallback to direct commands (will likely fail without sudo)
            logger.warning("Systemd recovery not available, attempting direct commands...")
            _run(['ip', 'link', 'set', self.cfg.interface, 'down'], timeout=10)
            time.sleep(2)

            _run(['systemctl', 'stop', 'wpa_supplicant'], timeout=15)
            _run(['systemctl', 'stop', 'networking'], timeout=15)
            time.sleep(3)

            _run(['ip', 'link', 'set', self.cfg.interface, 'up'], timeout=10)
            time.sleep(2)

            _run(['systemctl', 'start', 'networking'], timeout=30)
            time.sleep(3)
            _run(['systemctl', 'start', 'wpa_supplicant'], timeout=15)
            time.sleep(5)

            _run(['dhclient', self.cfg.interface], timeout=20)
            time.sleep(5)

            self._update_gateway_if_needed(force=True)

            logger.info("Hard network recovery completed (may be incomplete)")
            return True

        except Exception as e:
            logger.error(f"Hard recovery failed: {e}")
            return False

    def _determine_recovery_action(self) -> str:
        """Determine what recovery action to take based on failure count"""
        failures = self._recovery.consecutive_failures
        
        if failures >= self.cfg.critical_escalation_threshold:
            return "escalate"
        elif failures >= self.cfg.hard_recovery_threshold:
            return "hard"
        elif failures >= self.cfg.intermediate_recovery_threshold:
            return "intermediate"
        elif failures >= self.cfg.soft_recovery_threshold:
            return "soft"
        else:
            return "none"

    def _recover(self) -> bool:
        """Perform staged network recovery"""
        # CRITICAL: Skip ALL recovery if WiFi is intentionally disabled (LTE-first mode)
        # This ensures WiFi stays disabled when LTE is the primary connection
        if self._wifi_intentionally_disabled:
            logger.debug(
                "WiFi intentionally disabled - skipping ALL WiFi recovery "
                "(LTE-first mode active)"
            )
            return False

        current_time = time.time()

        # Increased from 30s to 120s in working config
        if current_time - self._recovery.last_recovery_time < 120:
            return False

        self._recovery.last_recovery_time = current_time
        recovery_action = self._determine_recovery_action()
        
        success = False
        
        if recovery_action == "soft":
            self._recovery.current_stage = "soft"
            self._recovery.soft_attempts += 1
            success = self._soft_recovery()
            
        elif recovery_action == "intermediate":
            self._recovery.current_stage = "intermediate"
            self._recovery.intermediate_attempts += 1
            success = self._intermediate_recovery()
            
        elif recovery_action == "hard":
            self._recovery.current_stage = "hard"
            self._recovery.hard_attempts += 1
            success = self._hard_recovery()
            
        elif recovery_action == "escalate":
            # Check if WiFi is intentionally disabled (e.g., LTE-first mode)
            # In this case, do NOT trigger WiFi provisioning - the device is using LTE
            if self._wifi_intentionally_disabled:
                logger.info(
                    "Network recovery exhausted but WiFi is intentionally disabled "
                    "(LTE-first mode) - skipping WiFi provisioning"
                )
                # Reset recovery state to prevent continuous escalation attempts
                self._recovery.reset()
                return False

            logger.error("Network recovery exhausted - triggering WiFi provisioning")

            # Report to error recovery system
            if self.error_recovery:
                self.error_recovery.report_error(
                    component="network",
                    error_type="connectivity_failure",
                    error_message=f"Network recovery failed after {self._recovery.consecutive_failures} attempts",
                    severity="critical",
                    context={
                        "consecutive_failures": self._recovery.consecutive_failures,
                        "soft_attempts": self._recovery.soft_attempts,
                        "intermediate_attempts": self._recovery.intermediate_attempts,
                        "hard_attempts": self._recovery.hard_attempts,
                        "interface": self.cfg.interface
                    }
                )

            # Trigger WiFi provisioning service
            self._trigger_wifi_provisioning()
            return False

        if recovery_action != "none":
            stage = recovery_action
            attempt_count = getattr(self._recovery, f"{stage}_attempts", 0)
            logger.info(f"Network {stage} recovery attempt #{attempt_count}: {'Success' if success else 'Failed'}")

        return success

    def _emit(self, cb: Optional[Callable[[dict], None]], payload: dict) -> None:
        """Safely emit callback"""
        try:
            if cb:
                cb(payload)
        except Exception as e:
            logger.warning(f"Callback error: {e}")

    def _run_loop(self) -> None:
        """Main monitoring loop with enhanced recovery"""
        if self.cfg.startup_delay_secs > 0:
            logger.info(f"Waiting {self.cfg.startup_delay_secs}s for network initialization...")
            time.sleep(self.cfg.startup_delay_secs)

        logger.info("Detecting network gateway...")
        self._update_gateway_if_needed(force=True)
        
        while not self._stop.is_set():
            try:
                ssid = self._get_ssid()
                rssi = self._get_rssi()
                wifi_ok = bool(ssid)

                internet_ok = False
                local_ping_ok = False
                public_ping_ok = False
                
                if wifi_ok:
                    local_ping_ok = self._ping(self.cfg.ping_target_local, timeout=3)
                    
                    if not local_ping_ok:
                        public_ping_ok = self._ping(self.cfg.ping_target_public, timeout=3)
                        self._failed_ping_count += 1

                        if public_ping_ok:
                            logger.warning(f"Gateway unreachable but internet OK (failed pings: {self._failed_ping_count})")
                            self._update_gateway_if_needed()
                    else:
                        self._failed_ping_count = 0
                    
                    internet_ok = local_ping_ok or public_ping_ok

                connectivity_ok = wifi_ok and internet_ok
                
                status = {
                    "ssid": ssid,
                    "rssi": rssi,
                    "wifi_ok": wifi_ok,
                    "internet_ok": internet_ok,
                    "connectivity_ok": connectivity_ok,
                    # Logging/diagnostics: expose whether WiFi is expected to be down
                    "wifi_intentionally_disabled": self._wifi_intentionally_disabled,
                    "gateway": self.cfg.ping_target_local,
                    "local_ping_ok": local_ping_ok,
                    "public_ping_ok": public_ping_ok,
                    "timestamp": time.time(),
                    "recovery_stage": self._recovery.current_stage,
                    "consecutive_failures": self._recovery.consecutive_failures
                }

                if self._wifi_intentionally_disabled and not connectivity_ok:
                    logger.debug(
                        "WiFi intentionally disabled; skipping interpretation of connectivity_ok=%s ssid=%r",
                        connectivity_ok,
                        ssid,
                    )

                self._emit(self.on_status, status)

                if not connectivity_ok:
                    # CRITICAL: Skip failure counting and recovery if WiFi is intentionally disabled
                    # This ensures WiFi stays disabled when LTE is the primary connection
                    if self._wifi_intentionally_disabled:
                        logger.debug(
                            "WiFi intentionally disabled - not counting connectivity failure "
                            "(LTE-first mode active, WiFi down is expected)"
                        )
                        # Don't count failures, don't trigger disconnect callback, don't attempt recovery
                        self._last_connected = False  # Track state but don't trigger callbacks
                    else:
                        self._recovery.consecutive_failures += 1

                        logger.warning(f"Network issue detected: Failure #{self._recovery.consecutive_failures}, WiFi: {wifi_ok}, Internet: {internet_ok}, SSID: {ssid}, RSSI: {rssi}dBm, Gateway: {self.cfg.ping_target_local} (reachable: {local_ping_ok}), Public IP: {self.cfg.ping_target_public} (reachable: {public_ping_ok}), Recovery stage: {self._recovery.current_stage}")

                        if self._last_connected:
                            logger.error(f"Network disconnected: WiFi={wifi_ok}, Internet={internet_ok}")
                            self._emit(self.on_disconnect, status)

                            if self.error_recovery:
                                severity = "medium" if self._recovery.consecutive_failures < 3 else "high"
                                self.error_recovery.report_error(
                                    component="network",
                                    error_type="connectivity_loss",
                                    error_message=f"Network connectivity lost (WiFi: {wifi_ok}, Internet: {internet_ok})",
                                    severity=severity,
                                    context={
                                        "ssid": ssid,
                                        "rssi": rssi,
                                        "consecutive_failures": self._recovery.consecutive_failures,
                                        "gateway": self.cfg.ping_target_local,
                                        "local_ping": local_ping_ok,
                                        "public_ping": public_ping_ok
                                    }
                                )

                        logger.info(f"Attempting recovery (backoff: {self._backoff:.1f}s)")
                        recovery_attempted = self._recover()
                        if recovery_attempted:
                            logger.info(f"Recovery executed, sleeping {min(self._backoff, 30):.1f}s")
                            time.sleep(min(self._backoff, 30))
                            self._backoff = min(self._backoff * 1.2, self.cfg.max_backoff_secs)
                        else:
                            logger.debug(f"Recovery skipped (too frequent), sleeping 10s")
                            time.sleep(10)
                        
                else:
                    had_failures = self._recovery.consecutive_failures > 0

                    if not self._last_connected:
                        logger.info(f"Network connected: {ssid} ({rssi}dBm) via {self.cfg.ping_target_local}")
                        self._emit(self.on_reconnect, status)

                        if self.error_recovery and had_failures:
                            self.error_recovery.report_success("network")

                    self._recovery.reset()
                    self._backoff = 5.0
                    self._failed_ping_count = 0

                    if rssi is not None and rssi <= self.cfg.weak_signal_threshold_dbm:
                        logger.warning(f"Weak WiFi signal: {rssi}dBm")
                        self._emit(self.on_status, {**status, "warning": "weak_signal"})

                self._last_connected = connectivity_ok

                check_interval = self.cfg.check_interval_secs
                
                slept = 0.0
                while slept < check_interval and not self._stop.is_set():
                    time.sleep(0.5)
                    slept += 0.5

            except Exception as e:
                logger.error(f"Network monitor error: {e}", exc_info=True)
                if self.error_recovery:
                    self.error_recovery.report_error(
                        component="network",
                        error_type="monitor_error",
                        error_message=str(e),
                        severity="medium",
                        context={"exception_type": type(e).__name__}
                    )
                time.sleep(5)

    def get_recovery_status(self) -> dict:
        """Get current recovery status and statistics"""
        return {
            "consecutive_failures": self._recovery.consecutive_failures,
            "current_stage": self._recovery.current_stage,
            "soft_attempts": self._recovery.soft_attempts,
            "intermediate_attempts": self._recovery.intermediate_attempts,
            "hard_attempts": self._recovery.hard_attempts,
            "last_recovery_time": self._recovery.last_recovery_time,
            "backoff_delay": self._backoff,
            "gateway": self.cfg.ping_target_local,
            "gateway_last_updated": self._gateway_last_updated,
            "failed_ping_count": self._failed_ping_count,
            "wifi_intentionally_disabled": self._wifi_intentionally_disabled
        }
