#!/usr/bin/env python3
"""
LTE Network Monitor for TSV6 Raspberry Pi

Monitors LTE connectivity via SIM7600NA-H 4G HAT and performs staged recovery.
Follows the same patterns as network_monitor.py (WiFi monitor).

Key fix: Use full paths for system commands to avoid PATH issues
"""

import subprocess
import threading
import time
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, TYPE_CHECKING, Dict, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .error_recovery import ErrorRecoverySystem
    from .systemd_recovery_manager import SystemdRecoveryManager
    from ..hardware.sim7600 import SIM7600Controller


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
class LTEMonitorConfig:
    """Configuration for LTE monitoring"""
    # Monitoring intervals
    check_interval_secs: float = 30.0  # Longer than WiFi since LTE is more stable
    startup_delay_secs: float = 15.0   # Wait for modem initialization

    # Signal thresholds (CSQ values: 0-31, 99=unknown)
    # CSQ 10 = ~-93 dBm (weak), CSQ 5 = ~-103 dBm (very weak)
    signal_weak_threshold_rssi: int = 10
    signal_critical_threshold_rssi: int = 5

    # Connectivity test
    ping_target: str = "8.8.8.8"
    ping_timeout_secs: int = 5
    wwan_interface: str = "wwan0"  # Network interface for LTE

    # Recovery thresholds (number of consecutive failures before action)
    soft_recovery_threshold: int = 2      # 60s to first recovery (2 * 30s)
    intermediate_recovery_threshold: int = 4  # 120s
    hard_recovery_threshold: int = 6      # 180s
    critical_escalation_threshold: int = 10   # 300s (5 min)

    # Backoff settings
    max_backoff_secs: float = 300.0
    initial_backoff_secs: float = 5.0

    # ModemManager mode: Use NetworkManager/ModemManager instead of AT commands
    # This is recommended when ModemManager is managing the modem (default on most distros)
    use_modemmanager: bool = True


class LTERecoveryStage:
    """Track LTE recovery stage and attempt counts"""
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


class LTEMonitor:
    """
    LTE connection monitoring and recovery system for IoT devices.

    Follows the same patterns as NetworkMonitor:
    - Background monitoring thread
    - Staged recovery with escalation
    - Error recovery system integration
    - Callbacks for state changes

    Staged Recovery:
    1. Soft (2 failures): Re-register to network (AT+CFUN=0 then AT+CFUN=1)
    2. Intermediate (4 failures): Restart PDP context (AT+CGACT=0,1 then AT+CGACT=1,1)
    3. Hard (6 failures): Full modem restart via serial
    4. Critical (10 failures): GPIO power cycle, escalate to system recovery
    """

    def __init__(
        self,
        lte_controller: 'SIM7600Controller',
        config: Optional[LTEMonitorConfig] = None,
        on_status: Optional[Callable[[dict], None]] = None,
        on_disconnect: Optional[Callable[[dict], None]] = None,
        on_reconnect: Optional[Callable[[dict], None]] = None,
        error_recovery_system: Optional['ErrorRecoverySystem'] = None,
        systemd_recovery_manager: Optional['SystemdRecoveryManager'] = None,
    ) -> None:
        """
        Initialize LTE monitor.

        Args:
            lte_controller: SIM7600Controller instance
            config: LTEMonitorConfig (uses defaults if None)
            on_status: Callback for status updates
            on_disconnect: Callback when LTE disconnects
            on_reconnect: Callback when LTE reconnects
            error_recovery_system: Optional error recovery integration
            systemd_recovery_manager: Optional systemd recovery manager
        """
        self.controller = lte_controller
        self.cfg = config or LTEMonitorConfig()
        self.on_status = on_status
        self.on_disconnect = on_disconnect
        self.on_reconnect = on_reconnect
        self.error_recovery = error_recovery_system
        self.systemd_recovery = systemd_recovery_manager

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_connected = None
        self._backoff = self.cfg.initial_backoff_secs
        self._recovery = LTERecoveryStage()

        # Track last good state for reconnect detection
        self._was_connected = False
        self._last_signal_quality: Tuple[int, int] = (99, 99)

        logger.info(f"LTE Monitor initialized (check interval: {self.cfg.check_interval_secs}s)")

    def start(self) -> None:
        """Start LTE monitoring in background thread"""
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="LTEMonitor",
            daemon=True
        )
        self._thread.start()
        logger.info("LTE monitoring started")

    def stop(self) -> None:
        """Stop LTE monitoring"""
        logger.info("Stopping LTE monitor...")
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("LTE monitor stopped")

    def _ping(self, host: str, count: int = 1, timeout: int = None, interface: str = None) -> bool:
        """Ping a host to test data connectivity"""
        timeout = timeout or self.cfg.ping_timeout_secs
        cmd = ["/bin/ping", "-c", str(count), "-W", str(timeout)]
        if interface:
            cmd.extend(["-I", interface])
        cmd.append(host)
        rc, _, _ = _run(cmd, timeout=timeout + 2)
        if rc != 0:
            # Fallback without full path
            cmd[0] = "ping"
            rc, _, _ = _run(cmd, timeout=timeout + 2)
        return rc == 0

    def _get_wwan_ip(self) -> str:
        """Get IP address of wwan interface (for ModemManager mode)"""
        try:
            rc, stdout, _ = _run(["ip", "-4", "addr", "show", self.cfg.wwan_interface])
            if rc == 0:
                for line in stdout.split('\n'):
                    line = line.strip()
                    if line.startswith('inet '):
                        # Parse: inet 10.232.10.9/30 brd ...
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[1].split('/')[0]
        except Exception as e:
            logger.debug(f"Failed to get wwan IP: {e}")
        return ''

    def _get_modemmanager_status(self) -> Dict[str, Any]:
        """Get modem status via mmcli (for ModemManager mode)"""
        status = {
            'state': 'unknown',
            'signal_quality': 0,
            'operator': '',
        }
        try:
            rc, stdout, _ = _run(["mmcli", "-m", "0"], timeout=10)
            if rc == 0:
                for line in stdout.split('\n'):
                    line = line.strip()
                    if 'state:' in line.lower() and 'power state' not in line.lower():
                        status['state'] = line.split(':')[-1].strip()
                    elif 'signal quality:' in line.lower():
                        # Parse: signal quality: 99% (recent)
                        qual = line.split(':')[-1].strip()
                        if '%' in qual:
                            status['signal_quality'] = int(qual.split('%')[0])
                    elif 'operator name:' in line.lower():
                        status['operator'] = line.split(':')[-1].strip()
        except Exception as e:
            logger.debug(f"Failed to get mmcli status: {e}")
        return status

    def _check_connectivity_modemmanager(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Check LTE connectivity using ModemManager/NetworkManager (passive mode).

        This doesn't use AT commands - just checks if wwan interface has IP and can ping.
        """
        status = {
            'connected': False,
            'data_connected': False,
            'signal_rssi': 99,
            'signal_dbm': -999,
            'signal_quality': 'unknown',
            'operator': '',
            'ip_address': '',
            'ping_success': False,
            'mode': 'modemmanager',
        }

        try:
            # Check if wwan interface has an IP
            ip_addr = self._get_wwan_ip()
            status['ip_address'] = ip_addr
            status['data_connected'] = bool(ip_addr)

            if ip_addr:
                status['connected'] = True
                # Test actual connectivity with ping via wwan interface
                status['ping_success'] = self._ping(
                    self.cfg.ping_target,
                    interface=self.cfg.wwan_interface
                )

            # Get additional info from ModemManager (non-blocking, just for status)
            mm_status = self._get_modemmanager_status()
            status['operator'] = mm_status.get('operator', '')
            signal_pct = mm_status.get('signal_quality', 0)

            # Convert percentage to approximate RSSI (0-31 scale)
            # 100% ≈ 31, 0% ≈ 0
            if signal_pct > 0:
                status['signal_rssi'] = int(signal_pct * 31 / 100)
                status['signal_quality'] = (
                    'excellent' if signal_pct >= 80 else
                    'good' if signal_pct >= 60 else
                    'fair' if signal_pct >= 40 else
                    'weak' if signal_pct >= 20 else
                    'critical'
                )

            is_connected = status['data_connected'] and status['ping_success']
            return is_connected, status

        except Exception as e:
            logger.error(f"ModemManager connectivity check error: {e}")
            status['error'] = str(e)
            return False, status

    def _check_connectivity(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Check LTE connectivity status.

        Returns:
            Tuple of (is_connected, status_dict)
        """
        # Use ModemManager mode if configured (default) - passive monitoring
        if self.cfg.use_modemmanager:
            return self._check_connectivity_modemmanager()

        # Legacy AT command mode (requires exclusive modem access)
        status = {
            'connected': False,
            'data_connected': False,
            'signal_rssi': 99,
            'signal_dbm': -999,
            'signal_quality': 'unknown',
            'operator': '',
            'ip_address': '',
            'ping_success': False,
        }

        try:
            # Check modem connection state
            if not self.controller.is_connected():
                status['error'] = 'modem_not_connected'
                return False, status

            status['connected'] = True

            # Get signal quality
            rssi, ber = self.controller.get_signal_quality()
            self._last_signal_quality = (rssi, ber)
            status['signal_rssi'] = rssi
            status['signal_dbm'] = self.controller.get_signal_dbm()

            # Classify signal quality
            if rssi == 99:
                status['signal_quality'] = 'unknown'
            elif rssi >= 20:
                status['signal_quality'] = 'excellent'
            elif rssi >= 15:
                status['signal_quality'] = 'good'
            elif rssi >= self.cfg.signal_weak_threshold_rssi:
                status['signal_quality'] = 'fair'
            elif rssi >= self.cfg.signal_critical_threshold_rssi:
                status['signal_quality'] = 'weak'
            else:
                status['signal_quality'] = 'critical'

            # Get network status
            net_status = self.controller.get_network_status()
            status['operator'] = net_status.get('operator', '')
            status['ip_address'] = net_status.get('ip_address', '')
            status['data_connected'] = net_status.get('data_connected', False)

            # Test actual data connectivity with ping
            if status['data_connected']:
                status['ping_success'] = self._ping(self.cfg.ping_target)

            # Overall connectivity is data connected + ping success
            is_connected = status['data_connected'] and status['ping_success']
            return is_connected, status

        except Exception as e:
            logger.error(f"Connectivity check error: {e}")
            status['error'] = str(e)
            return False, status

    def _soft_recovery(self) -> bool:
        """
        Perform soft recovery: Re-register to network.

        ModemManager mode: Restart NetworkManager connection
        AT mode: AT+CFUN=0 (minimum functionality) then AT+CFUN=1 (full functionality)
        """
        try:
            logger.info("Performing soft LTE recovery (network re-registration)...")
            self._recovery.soft_attempts += 1
            self._recovery.current_stage = "soft"

            if self.cfg.use_modemmanager:
                # ModemManager mode: cycle the NetworkManager connection
                logger.info("Soft recovery via NetworkManager...")
                rc, _, _ = _run(["sudo", "nmcli", "connection", "down", "hologram-lte"], timeout=10)
                time.sleep(2)
                rc, _, _ = _run(["sudo", "nmcli", "connection", "up", "hologram-lte"], timeout=30)
                time.sleep(5)
                # Check if we got an IP
                ip = self._get_wwan_ip()
                if ip:
                    logger.info(f"Soft LTE recovery completed - got IP {ip}")
                    return True
                logger.warning("Soft LTE recovery - no IP after reconnect")
                return False

            # Legacy AT command mode
            from ..hardware.sim7600.at_commands import ATCommands

            # Minimum functionality mode
            success, _ = self.controller._send_command(ATCommands.MINIMUM_FUNCTIONALITY)
            if not success:
                logger.warning("Failed to enter minimum functionality mode")

            time.sleep(3)

            # Full functionality mode
            success, _ = self.controller._send_command(ATCommands.FULL_FUNCTIONALITY)
            if not success:
                logger.warning("Failed to restore full functionality")
                return False

            time.sleep(5)

            # Wait for network re-registration
            success = self.controller._wait_for_registration(timeout=30)

            if success:
                logger.info("Soft LTE recovery completed - network re-registered")
            else:
                logger.warning("Soft LTE recovery - registration timeout")

            return success

        except Exception as e:
            logger.error(f"Soft LTE recovery failed: {e}")
            return False

    def _intermediate_recovery(self) -> bool:
        """
        Perform intermediate recovery: Restart PDP context.

        ModemManager mode: Reset modem via mmcli
        AT mode: AT+CGACT=0,1 (deactivate) then AT+CGACT=1,1 (activate)
        """
        try:
            logger.info("Performing intermediate LTE recovery (PDP context restart)...")
            self._recovery.intermediate_attempts += 1
            self._recovery.current_stage = "intermediate"

            if self.cfg.use_modemmanager:
                # ModemManager mode: reset modem
                logger.info("Intermediate recovery via ModemManager reset...")
                rc, _, _ = _run(["sudo", "mmcli", "-m", "0", "-r"], timeout=30)
                time.sleep(15)  # Wait for modem to restart
                # Reconnect
                rc, _, _ = _run(["sudo", "nmcli", "connection", "up", "hologram-lte"], timeout=60)
                time.sleep(10)
                ip = self._get_wwan_ip()
                if ip:
                    logger.info(f"Intermediate LTE recovery completed - got IP {ip}")
                    return True
                logger.warning("Intermediate LTE recovery - no IP after modem reset")
                return False

            from ..hardware.sim7600.at_commands import ATCommands

            # Deactivate PDP context
            deactivate_cmd = ATCommands.deactivate_pdp(1)
            self.controller._send_command(deactivate_cmd, check_ok=False)
            time.sleep(2)

            # Detach from GPRS
            self.controller._send_command(ATCommands.DETACH_GPRS, check_ok=False)
            time.sleep(3)

            # Re-attach to GPRS
            success, _ = self.controller._send_command(ATCommands.ATTACH_GPRS)
            if not success:
                logger.warning("GPRS re-attach failed")

            time.sleep(2)

            # Activate PDP context
            activate_cmd = ATCommands.activate_pdp(1)
            success, _ = self.controller._send_command(activate_cmd)
            if not success:
                logger.warning("PDP context activation failed")
                return False

            time.sleep(3)

            # Re-establish NDIS connection
            success, _ = self.controller._send_command(ATCommands.NDIS_CONNECT)
            if success:
                logger.info("Intermediate LTE recovery completed - PDP context restarted")
                return True

            logger.warning("Intermediate LTE recovery - NDIS connection failed")
            return False

        except Exception as e:
            logger.error(f"Intermediate LTE recovery failed: {e}")
            return False

    def _hard_recovery(self) -> bool:
        """
        Perform hard recovery: Full modem restart.

        ModemManager mode: Restart ModemManager service
        AT mode: Full modem restart via serial
        """
        try:
            logger.info("Performing hard LTE recovery (modem restart)...")
            self._recovery.hard_attempts += 1
            self._recovery.current_stage = "hard"

            if self.cfg.use_modemmanager:
                # ModemManager mode: restart ModemManager service
                logger.info("Hard recovery - restarting ModemManager service...")
                _run(["sudo", "systemctl", "restart", "ModemManager"], timeout=30)
                time.sleep(20)  # Wait for service restart and modem detection
                # Reconnect
                rc, _, _ = _run(["sudo", "nmcli", "connection", "up", "hologram-lte"], timeout=60)
                time.sleep(10)
                ip = self._get_wwan_ip()
                if ip:
                    logger.info(f"Hard LTE recovery completed - got IP {ip}")
                    return True
                logger.warning("Hard LTE recovery - no IP after service restart")
                return False

            # Legacy AT mode: Full modem restart
            success = self.controller.restart_modem()

            if success:
                logger.info("Hard LTE recovery completed - modem restarted")
            else:
                logger.warning("Hard LTE recovery - modem restart failed")

            return success

        except Exception as e:
            logger.error(f"Hard LTE recovery failed: {e}")
            return False

    def _critical_recovery(self) -> bool:
        """
        Perform critical recovery: GPIO power cycle.
        """
        try:
            logger.info("Performing critical LTE recovery (GPIO power cycle)...")
            self._recovery.current_stage = "critical"

            # Power cycle via GPIO
            success = self.controller.power_cycle()

            if success:
                logger.info("Critical LTE recovery completed - power cycle successful")
            else:
                logger.warning("Critical LTE recovery - power cycle failed")

            # Report to error recovery system for potential escalation
            if self.error_recovery:
                self.error_recovery.report_error(
                    "lte_modem",
                    "critical_recovery",
                    "LTE modem required critical recovery (power cycle)",
                    severity="critical"
                )

            return success

        except Exception as e:
            logger.error(f"Critical LTE recovery failed: {e}")
            return False

    def _run_loop(self) -> None:
        """Main monitoring loop"""
        logger.info(f"LTE monitor loop starting (delay: {self.cfg.startup_delay_secs}s)")

        # Initial startup delay
        if self._stop.wait(self.cfg.startup_delay_secs):
            return

        while not self._stop.is_set():
            try:
                # Check connectivity
                is_connected, status = self._check_connectivity()

                # Handle state transitions
                if is_connected:
                    # Connection good
                    if not self._was_connected:
                        # Just reconnected
                        logger.info("LTE connection restored")
                        self._recovery.reset()
                        self._backoff = self.cfg.initial_backoff_secs
                        if self.on_reconnect:
                            try:
                                self.on_reconnect(status)
                            except Exception as e:
                                logger.error(f"Reconnect callback error: {e}")

                    self._was_connected = True
                    self._last_connected = time.time()

                    # Report success to error recovery
                    if self.error_recovery:
                        self.error_recovery.report_success("lte_modem")

                else:
                    # Connection failed
                    self._recovery.consecutive_failures += 1
                    failures = self._recovery.consecutive_failures

                    if self._was_connected:
                        # Just disconnected
                        logger.warning(f"LTE connection lost: {status.get('error', 'unknown')}")
                        if self.on_disconnect:
                            try:
                                self.on_disconnect(status)
                            except Exception as e:
                                logger.error(f"Disconnect callback error: {e}")

                    self._was_connected = False

                    # Report failure to error recovery
                    if self.error_recovery:
                        self.error_recovery.report_error(
                            "lte_modem",
                            "connectivity_lost",
                            f"LTE connectivity lost (failures: {failures})",
                            severity="high" if failures >= self.cfg.soft_recovery_threshold else "low"
                        )

                    # Perform staged recovery based on failure count
                    if failures >= self.cfg.critical_escalation_threshold:
                        logger.warning(f"Critical threshold reached ({failures} failures), attempting power cycle")
                        self._critical_recovery()
                        self._recovery.consecutive_failures = 0  # Reset after critical action

                    elif failures >= self.cfg.hard_recovery_threshold:
                        logger.warning(f"Hard threshold reached ({failures} failures), restarting modem")
                        self._hard_recovery()

                    elif failures >= self.cfg.intermediate_recovery_threshold:
                        logger.warning(f"Intermediate threshold reached ({failures} failures), restarting PDP")
                        self._intermediate_recovery()

                    elif failures >= self.cfg.soft_recovery_threshold:
                        logger.warning(f"Soft threshold reached ({failures} failures), re-registering network")
                        self._soft_recovery()

                # Always call status callback
                if self.on_status:
                    try:
                        status['recovery_stage'] = self._recovery.current_stage
                        status['consecutive_failures'] = self._recovery.consecutive_failures
                        self.on_status(status)
                    except Exception as e:
                        logger.error(f"Status callback error: {e}")

                # Check for weak signal warning
                rssi = status.get('signal_rssi', 99)
                if rssi != 99 and rssi <= self.cfg.signal_critical_threshold_rssi:
                    logger.warning(f"LTE signal critically weak: RSSI={rssi} ({status.get('signal_dbm', -999)} dBm)")

            except Exception as e:
                logger.error(f"LTE monitor loop error: {e}")

            # Wait for next check interval
            self._stop.wait(self.cfg.check_interval_secs)

        logger.info("LTE monitor loop exited")

    def get_recovery_status(self) -> Dict[str, Any]:
        """Get current recovery status and statistics"""
        return {
            'current_stage': self._recovery.current_stage,
            'consecutive_failures': self._recovery.consecutive_failures,
            'soft_attempts': self._recovery.soft_attempts,
            'intermediate_attempts': self._recovery.intermediate_attempts,
            'hard_attempts': self._recovery.hard_attempts,
            'last_recovery_time': self._recovery.last_recovery_time,
            'last_signal_quality': self._last_signal_quality,
            'is_connected': self._was_connected,
            'last_connected_time': self._last_connected,
        }

    def force_reconnect(self) -> bool:
        """Force a reconnection attempt"""
        logger.info("Forcing LTE reconnect...")
        return self._hard_recovery()
