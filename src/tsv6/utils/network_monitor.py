#!/usr/bin/env python3
"""
Network Monitor for Raspberry Pi — OBSERVE ONLY

This monitor checks WiFi association, signal strength, and internet
reachability every 10 seconds and emits callbacks so the rest of the
application can react (e.g. update status indicator, log to AWS).

It does NOT perform any recovery actions.  Recovery is handled by:
  Layer 0 — NetworkManager  (autoconnect-retries=0 = infinite)
  Layer 2 — Shell watchdog  (tsv6-network-watchdog.sh → reboot-force)
  Layer 3 — HW watchdog     (BCM2835, RuntimeWatchdogSec=15)

See docs/WIFI_HARDENING.md for the full architecture.
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
    check_interval_secs: float = 10.0
    weak_signal_threshold_dbm: int = -80
    ping_target_local: str = "8.8.8.8"
    ping_target_public: str = "8.8.8.8"
    max_backoff_secs: float = 300.0
    # Thresholds retained for backward compatibility only — they no longer
    # drive any recovery actions.  Severity is reported to ErrorRecoverySystem
    # for observability (medium < 6, high >= 6).
    soft_recovery_threshold: int = 2
    intermediate_recovery_threshold: int = 4
    hard_recovery_threshold: int = 6
    critical_escalation_threshold: int = 12
    startup_delay_secs: float = 10.0
    gateway_retry_count: int = 3


class NetworkMonitor:
    """
    Observe-only network monitor for IoT devices.

    Checks WiFi association, RSSI, and ping reachability on a fixed interval
    and emits callbacks.  Does NOT perform any recovery — that is handled by
    NetworkManager (Layer 0) and the shell watchdog (Layer 2).
    """

    def __init__(
        self,
        config: Optional[NetworkMonitorConfig] = None,
        on_status: Optional[Callable[[dict], None]] = None,
        on_disconnect: Optional[Callable[[dict], None]] = None,
        on_reconnect: Optional[Callable[[dict], None]] = None,
        error_recovery_system: Optional['ErrorRecoverySystem'] = None,
        # Accepted for API compatibility — not used (recovery is external)
        systemd_recovery_manager: Optional['SystemdRecoveryManager'] = None,
    ) -> None:
        self.cfg = config or NetworkMonitorConfig()
        self.on_status = on_status
        self.on_disconnect = on_disconnect
        self.on_reconnect = on_reconnect
        self.error_recovery = error_recovery_system

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_connected = None
        self._consecutive_failures = 0
        self._gateway_last_updated = 0
        self._failed_ping_count = 0

        # Flag to indicate WiFi is intentionally disabled (e.g., for LTE-first mode)
        # When True, network monitor skips failure counting and callbacks
        self._wifi_intentionally_disabled = False

        logger.info(
            "Network Monitor initialized for %s (observe-only, "
            "recovery via NM + shell watchdog)",
            self.cfg.interface,
        )

    # ------------------------------------------------------------------
    # WiFi intentionally-disabled helpers (LTE-first mode)
    # ------------------------------------------------------------------

    def set_wifi_intentionally_disabled(self, disabled: bool) -> None:
        """Mark WiFi as intentionally down (LTE-first mode)."""
        self._wifi_intentionally_disabled = disabled
        if disabled:
            logger.info("WiFi marked as intentionally disabled — provisioning will be skipped")
            self._consecutive_failures = 0
        else:
            logger.info("WiFi marked as active — normal monitoring restored")

    def is_wifi_intentionally_disabled(self) -> bool:
        return self._wifi_intentionally_disabled

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Observation helpers
    # ------------------------------------------------------------------

    def _get_ssid(self) -> str:
        """Get current WiFi SSID"""
        rc, out, err = _run(["/usr/sbin/iwgetid", "-r"])
        if rc == 0 and out:
            return out

        rc, out, err = _run(["iwgetid", "-r"])
        if rc == 0 and out:
            return out

        if rc != 0:
            logger.warning("iwgetid failed: rc=%d, err='%s'", rc, err)
        return ""

    def _get_rssi(self) -> Optional[int]:
        """Get WiFi signal strength in dBm"""
        rc, out, _ = _run(["/usr/sbin/iwconfig", self.cfg.interface])
        if rc != 0:
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
                                logger.info("Gateway detected: %s (attempt %d/%d)", gateway, attempt + 1, attempts)
                            return gateway
                        except Exception:
                            pass

            if attempt < attempts - 1:
                time.sleep(2)

        logger.warning("Using default gateway: %s", self.cfg.ping_target_local)
        return self.cfg.ping_target_local

    def _update_gateway_if_needed(self, force: bool = False) -> None:
        """Update gateway address if needed"""
        current_time = time.time()

        should_update = (
            force
            or self._gateway_last_updated == 0
            or (self._failed_ping_count >= 2 and current_time - self._gateway_last_updated > 60)
        )

        if should_update:
            logger.info("Re-detecting gateway...")
            old_gateway = self.cfg.ping_target_local
            new_gateway = self._get_gateway(retry=True)

            if new_gateway != old_gateway:
                logger.info("Gateway updated: %s → %s", old_gateway, new_gateway)
                self.cfg.ping_target_local = new_gateway
                self._failed_ping_count = 0
            else:
                logger.debug("Gateway unchanged: %s", new_gateway)

            self._gateway_last_updated = current_time

    # ------------------------------------------------------------------
    # Callback helper
    # ------------------------------------------------------------------

    def _emit(self, cb: Optional[Callable[[dict], None]], payload: dict) -> None:
        try:
            if cb:
                cb(payload)
        except Exception as e:
            logger.warning("Callback error: %s", e)

    # ------------------------------------------------------------------
    # Main loop — observe only, NO recovery actions
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        if self.cfg.startup_delay_secs > 0:
            logger.info("Waiting %.0fs for network initialization...", self.cfg.startup_delay_secs)
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
                            logger.warning(
                                "Gateway unreachable but internet OK (failed pings: %d)",
                                self._failed_ping_count,
                            )
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
                    "wifi_intentionally_disabled": self._wifi_intentionally_disabled,
                    "gateway": self.cfg.ping_target_local,
                    "local_ping_ok": local_ping_ok,
                    "public_ping_ok": public_ping_ok,
                    "timestamp": time.time(),
                    "recovery_stage": "none",
                    "consecutive_failures": self._consecutive_failures,
                }

                self._emit(self.on_status, status)

                if not connectivity_ok:
                    if self._wifi_intentionally_disabled:
                        # WiFi is expected to be down in LTE-first mode — don't
                        # count failures or fire disconnect callbacks.
                        logger.debug(
                            "WiFi intentionally disabled — not counting failure "
                            "(LTE-first mode active)"
                        )
                        self._last_connected = False
                    else:
                        self._consecutive_failures += 1
                        logger.warning(
                            "Network issue #%d: WiFi=%s Internet=%s SSID=%s "
                            "RSSI=%sdBm GW=%s (reach=%s) Public=%s (reach=%s)",
                            self._consecutive_failures,
                            wifi_ok, internet_ok, ssid, rssi,
                            self.cfg.ping_target_local, local_ping_ok,
                            self.cfg.ping_target_public, public_ping_ok,
                        )

                        if self._last_connected:
                            logger.error("Network disconnected: WiFi=%s, Internet=%s", wifi_ok, internet_ok)
                            self._emit(self.on_disconnect, status)

                            if self.error_recovery:
                                severity = "medium" if self._consecutive_failures < 6 else "high"
                                self.error_recovery.report_error(
                                    component="network",
                                    error_type="connectivity_loss",
                                    error_message=f"Network connectivity lost (WiFi: {wifi_ok}, Internet: {internet_ok})",
                                    severity=severity,
                                    context={
                                        "ssid": ssid,
                                        "rssi": rssi,
                                        "consecutive_failures": self._consecutive_failures,
                                        "gateway": self.cfg.ping_target_local,
                                        "local_ping": local_ping_ok,
                                        "public_ping": public_ping_ok,
                                    },
                                )

                        # NO recovery action — NM (Layer 0) and shell watchdog
                        # (Layer 2) handle reconnection.  Adding recovery here
                        # would cause two systems to fight, making things worse.

                else:
                    had_failures = self._consecutive_failures > 0

                    if not self._last_connected:
                        logger.info("Network connected: %s (%sdBm) via %s", ssid, rssi, self.cfg.ping_target_local)
                        self._emit(self.on_reconnect, status)

                        if self.error_recovery and had_failures:
                            self.error_recovery.report_success("network")

                    self._consecutive_failures = 0
                    self._failed_ping_count = 0

                    if rssi is not None and rssi <= self.cfg.weak_signal_threshold_dbm:
                        logger.warning("Weak WiFi signal: %ddBm", rssi)
                        self._emit(self.on_status, {**status, "warning": "weak_signal"})

                self._last_connected = connectivity_ok

                # Fixed interval — no backoff, no extra sleeps
                slept = 0.0
                while slept < self.cfg.check_interval_secs and not self._stop.is_set():
                    time.sleep(0.5)
                    slept += 0.5

            except Exception as e:
                logger.error("Network monitor error: %s", e, exc_info=True)
                if self.error_recovery:
                    self.error_recovery.report_error(
                        component="network",
                        error_type="monitor_error",
                        error_message=str(e),
                        severity="medium",
                        context={"exception_type": type(e).__name__},
                    )
                time.sleep(5)

    # ------------------------------------------------------------------
    # Status (backward-compatible — recovery fields are always zero)
    # ------------------------------------------------------------------

    def get_recovery_status(self) -> dict:
        """Get current monitoring status and statistics"""
        return {
            "consecutive_failures": self._consecutive_failures,
            "current_stage": "none",
            "soft_attempts": 0,
            "intermediate_attempts": 0,
            "hard_attempts": 0,
            "last_recovery_time": 0,
            "backoff_delay": 0,
            "gateway": self.cfg.ping_target_local,
            "gateway_last_updated": self._gateway_last_updated,
            "failed_ping_count": self._failed_ping_count,
            "wifi_intentionally_disabled": self._wifi_intentionally_disabled,
        }
