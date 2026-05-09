#!/usr/bin/env python3
"""
SystemD Recovery Manager for TSV6

Provides a secure interface to invoke privileged recovery actions
via root-owned systemd helper units using D-Bus.
"""

import logging
import subprocess
import time
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)

try:
    import dbus
    HAVE_DBUS = True
except ImportError:
    HAVE_DBUS = False
    logger.warning("'dbus' Python package not found. SystemD recovery will be disabled.")
    logger.warning("To install: pip install dbus-python")


class RecoveryAction(Enum):
    """Recovery action types"""
    SOFT = "soft"
    INTERMEDIATE = "intermediate"
    HARD = "hard"


class SystemdRecoveryManager:
    """
    Manages privileged recovery actions by invoking systemd helper units via D-Bus.
    
    This class replaces direct sudo calls with secure D-Bus communication,
    allowing the unprivileged application to trigger root-level recovery actions.
    """
    
    # Mapping of recovery actions to systemd service names
    RECOVERY_SERVICES = {
        RecoveryAction.SOFT: "tsv6-recovery-soft.service",
        RecoveryAction.INTERMEDIATE: "tsv6-recovery-intermediate.service", 
        RecoveryAction.HARD: "tsv6-recovery-hard.service"
    }
    
    def __init__(self, interface: str = "wlan0"):
        """
        Initialize the recovery manager.
        
        Args:
            interface: Network interface name to recover (default: wlan0)
        """
        self.interface = interface
        self.logger = logging.getLogger(__name__)
        self._bus = None
        self._systemd = None
        
        # Initialize D-Bus connection
        if HAVE_DBUS:
            self._init_dbus()
        else:
            self.logger.error("D-Bus not available, recovery manager disabled")
    
    def _init_dbus(self) -> bool:
        """Initialize D-Bus connection to systemd"""
        try:
            # Connect to system bus
            self._bus = dbus.SystemBus()
            
            # Get systemd manager interface
            systemd_proxy = self._bus.get_object(
                'org.freedesktop.systemd1',
                '/org/freedesktop/systemd1'
            )
            self._systemd = dbus.Interface(
                systemd_proxy,
                'org.freedesktop.systemd1.Manager'
            )
            
            self.logger.info("Successfully connected to systemd via D-Bus")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to connect to systemd via D-Bus: {e}")
            self._bus = None
            self._systemd = None
            return False
    
    def is_available(self) -> bool:
        """Check if D-Bus and systemd are available"""
        return HAVE_DBUS and self._systemd is not None
    
    def _start_unit(self, service_name: str) -> bool:
        """
        Start a systemd unit via D-Bus.
        
        Args:
            service_name: Name of the systemd service to start
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_available():
            self.logger.error("D-Bus/systemd not available, cannot start recovery unit")
            return False
        
        try:
            self.logger.info(f"Starting recovery unit: {service_name}")
            
            # Start the unit with interface as argument
            # The 'replace' mode ensures the unit replaces any existing job
            job_path = self._systemd.StartUnit(
                f"{service_name}:{self.interface}",
                'replace'
            )
            
            # Wait for job to complete (optional, but good for feedback)
            # We'll poll for a short time to see if it completes quickly
            job_id = job_path.split('/')[-1]
            timeout = 5  # seconds
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                try:
                    job_proxy = self._bus.get_object(
                        'org.freedesktop.systemd1',
                        job_path
                    )
                    job_interface = dbus.Interface(
                        job_proxy,
                        'org.freedesktop.systemd1.Job'
                    )
                    
                    job_state = job_interface.Get('org.freedesktop.systemd1.Job', 'State')
                    
                    if job_state == 'done':
                        self.logger.info(f"Recovery unit {service_name} completed successfully")
                        return True
                    elif job_state == 'failed':
                        self.logger.error(f"Recovery unit {service_name} failed")
                        return False
                    
                except dbus.exceptions.DBusException:
                    # Job might have already been removed
                    break
                
                time.sleep(0.5)
            
            # If we didn't get a definitive answer, assume it was queued
            self.logger.info(f"Recovery unit {service_name} started (job queued)")
            return True
            
        except dbus.exceptions.DBusException as e:
            self.logger.error(f"D-Bus error starting unit {service_name}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error starting unit {service_name}: {e}")
            return False
    
    def execute_recovery(self, action: RecoveryAction) -> bool:
        """
        Execute a recovery action.
        
        Args:
            action: The recovery action to execute
            
        Returns:
            True if the action was successfully started, False otherwise
        """
        service_name = self.RECOVERY_SERVICES.get(action)
        if not service_name:
            self.logger.error(f"Unknown recovery action: {action}")
            return False

        logger.info(f"Executing {action.value} network recovery via systemd helper...")
        return self._start_unit(service_name)
    
    def execute_soft_recovery(self) -> bool:
        """Execute soft network recovery"""
        return self.execute_recovery(RecoveryAction.SOFT)
    
    def execute_intermediate_recovery(self) -> bool:
        """Execute intermediate network recovery"""
        return self.execute_recovery(RecoveryAction.INTERMEDIATE)
    
    def execute_hard_recovery(self) -> bool:
        """Execute hard network recovery"""
        return self.execute_recovery(RecoveryAction.HARD)
    
    def execute_system_reboot(self) -> bool:
        """
        Execute system reboot via systemd.
        
        This is safer than direct 'sudo reboot' as it works with systemd's
        permission model and doesn't require passwordless sudo.
        
        Returns:
            True if reboot was initiated, False otherwise
        """
        if not self.is_available():
            self.logger.error("D-Bus/systemd not available, cannot initiate reboot")
            return False
        
        try:
            self.logger.critical("Initiating system reboot via systemd...")
            
            # Use systemd's Reboot method via D-Bus
            # This is the proper way to reboot from an unprivileged process
            self._systemd.Reboot()
            
            # If we get here, reboot was accepted by systemd
            self.logger.critical("System reboot accepted by systemd")
            return True
            
        except dbus.exceptions.DBusException as e:
            self.logger.error(f"D-Bus error initiating reboot: {e}")
            return self._execute_systemctl_reboot_fallback()
                
        except Exception as e:
            self.logger.error(f"Unexpected error initiating reboot: {e}")
            return False

    def _execute_systemctl_reboot_fallback(self) -> bool:
        """
        Try unprivileged ``systemctl reboot`` and report whether it was accepted.

        ``systemctl reboot`` returns a non-zero code when polkit requires
        interactive authentication. Treating that as success blocks the
        caller's stronger fallback path, leaving the kiosk up after the network
        reboot deadline has already been exceeded.
        """
        try:
            self.logger.warning("Falling back to systemctl reboot...")
            result = subprocess.run(
                ['systemctl', 'reboot'],
                timeout=5,
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception as fallback_error:
            self.logger.error(f"Fallback reboot failed: {fallback_error}")
            return False

        if result.returncode == 0:
            self.logger.critical("System reboot via systemctl accepted")
            return True

        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or "no output"
        self.logger.error(
            "Fallback systemctl reboot failed with return code %s: %s",
            result.returncode,
            detail,
        )
        return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get the status of the recovery manager"""
        return {
            "available": self.is_available(),
            "interface": self.interface,
            "dbus_available": HAVE_DBUS,
            "services": list(self.RECOVERY_SERVICES.values())
        }


# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    manager = SystemdRecoveryManager()

    logger.info("SystemD Recovery Manager Status:")
    status = manager.get_status()
    for key, value in status.items():
        logger.info(f"  {key}: {value}")

    if manager.is_available():
        logger.info("\nTesting soft recovery...")
        success = manager.execute_soft_recovery()
        logger.info(f"Soft recovery started: {success}")
    else:
        logger.info("\nRecovery manager not available, cannot test")
