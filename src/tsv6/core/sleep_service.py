#!/usr/bin/env python3
"""
TSV6 Sleep Service

Manages device sleep mode for power saving:
- Stops tsv6@g1tech.service at sleep time
- Displays sleep message on screen
- Publishes sleep status to AWS then disconnects
- Wakes and restarts tsv6@g1tech.service at wake time
"""

import sys
import os
import time
import logging
import subprocess
import signal
import datetime
from datetime import timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tsv6.config.production_config import ProductionConfigManager
from tsv6.utils.display_manager import DisplayManager

try:
    from tsv6.core.aws_resilient_manager import ResilientAWSManager, RetryConfig
    from tsv6.utils.version import get_firmware_version
    AWS_AVAILABLE = True
except ImportError:
    AWS_AVAILABLE = False
    def get_firmware_version():
        return "unknown"


class SleepService:
    """Manages device sleep mode for power saving"""

    # Service management constants
    SERVICE_STOP_TIMEOUT_SECS = 30
    SERVICE_START_TIMEOUT_SECS = 30
    SERVICE_STATE_CHECK_TIMEOUT_SECS = 10
    SERVICE_TRANSITION_DELAY_SECS = 2
    MAIN_LOOP_INTERVAL_SECS = 30
    ERROR_RECOVERY_INTERVAL_SECS = 60
    AWS_PUBLISH_DELAY_SECS = 1

    def __init__(self):
        self.running = True
        self.sleeping = False
        self.display_manager: Optional[DisplayManager] = None
        self.aws_manager: Optional[ResilientAWSManager] = None

        self.config_manager = ProductionConfigManager()
        self.sleep_config = self.config_manager.sleep_config

        self._setup_logging()
        self.logger = logging.getLogger(__name__)

        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self.logger.info("Sleep service initialized")
        self.logger.info(f"Sleep time: {self.sleep_config.sleep_time}")
        self.logger.info(f"Wake time: {self.sleep_config.wake_time}")
        self.logger.info(f"Managing service: {self.tsv6_service}")

    @property
    def tsv6_service(self) -> str:
        """Get the tsv6 service name from config"""
        return self.sleep_config.tsv6_service_name

    def _setup_logging(self):
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            handlers=[
                logging.StreamHandler(),
            ]
        )

    def _signal_handler(self, signum, _frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        if self.sleeping:
            self._wake_up()
    
    def _parse_time(self, time_str: str) -> tuple:
        """Parse time string HH:MM to (hour, minute) tuple with validation"""
        try:
            parts = time_str.split(':')
            if len(parts) != 2:
                raise ValueError(f"Invalid time format: {time_str}")
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour <= 23):
                raise ValueError(f"Hour must be 0-23, got {hour}")
            if not (0 <= minute <= 59):
                raise ValueError(f"Minute must be 0-59, got {minute}")
            return hour, minute
        except (ValueError, IndexError) as e:
            self.logger.error(f"Invalid time string '{time_str}': {e}")
            raise ValueError(f"Invalid time format: {time_str}. Expected HH:MM")
    
    def _is_sleep_time(self) -> bool:
        """Check if current time is within sleep period"""
        now = datetime.datetime.now()
        current_minutes = now.hour * 60 + now.minute
        
        sleep_h, sleep_m = self._parse_time(self.sleep_config.sleep_time)
        wake_h, wake_m = self._parse_time(self.sleep_config.wake_time)
        
        sleep_minutes = sleep_h * 60 + sleep_m
        wake_minutes = wake_h * 60 + wake_m
        
        if sleep_minutes > wake_minutes:
            return current_minutes >= sleep_minutes or current_minutes < wake_minutes
        else:
            return sleep_minutes <= current_minutes < wake_minutes
    
    def _get_wake_time_display(self) -> str:
        """Get formatted wake time for display"""
        wake_h, wake_m = self._parse_time(self.sleep_config.wake_time)
        wake_dt = datetime.datetime.now().replace(hour=wake_h, minute=wake_m, second=0)
        
        if wake_dt <= datetime.datetime.now():
            wake_dt += timedelta(days=1)
        
        return wake_dt.strftime("%-I:%M %p").lower()
    
    def _wait_for_service_state(self, target_state: str, timeout: int = None) -> bool:
        """Wait for service to reach target state (active/inactive)"""
        if timeout is None:
            timeout = self.SERVICE_STATE_CHECK_TIMEOUT_SECS
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', self.tsv6_service],
                    capture_output=True, text=True, timeout=5
                )
                current_state = result.stdout.strip()
                if target_state == "inactive" and current_state in ["inactive", "failed"]:
                    return True
                if target_state == "active" and current_state == "active":
                    return True
            except subprocess.TimeoutExpired:
                pass
            time.sleep(1)
        return False

    def _stop_tsv6_service(self) -> bool:
        """Stop the tsv6 service with verification"""
        try:
            self.logger.info(f"Stopping {self.tsv6_service}...")
            result = subprocess.run(
                ['sudo', 'systemctl', 'stop', self.tsv6_service],
                capture_output=True,
                text=True,
                timeout=self.SERVICE_STOP_TIMEOUT_SECS
            )
            if result.returncode != 0:
                self.logger.error(f"Failed to stop {self.tsv6_service}: {result.stderr}")
                return False

            # Verify service actually stopped
            if self._wait_for_service_state("inactive"):
                self.logger.info(f"{self.tsv6_service} stopped successfully")
                return True
            else:
                self.logger.warning(f"{self.tsv6_service} stop command succeeded but service still active")
                return False
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout stopping {self.tsv6_service}")
            return False
        except subprocess.SubprocessError as e:
            self.logger.error(f"Subprocess error stopping {self.tsv6_service}: {e}")
            return False
        except OSError as e:
            self.logger.error(f"OS error stopping {self.tsv6_service}: {e}")
            return False

    def _start_tsv6_service(self) -> bool:
        """Start the tsv6 service with verification"""
        try:
            self.logger.info(f"Starting {self.tsv6_service}...")
            result = subprocess.run(
                ['sudo', 'systemctl', 'start', self.tsv6_service],
                capture_output=True,
                text=True,
                timeout=self.SERVICE_START_TIMEOUT_SECS
            )
            if result.returncode != 0:
                self.logger.error(f"Failed to start {self.tsv6_service}: {result.stderr}")
                return False

            # Verify service actually started
            if self._wait_for_service_state("active"):
                self.logger.info(f"{self.tsv6_service} started successfully")
                return True
            else:
                self.logger.warning(f"{self.tsv6_service} start command succeeded but service not active")
                return False
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout starting {self.tsv6_service}")
            return False
        except subprocess.SubprocessError as e:
            self.logger.error(f"Subprocess error starting {self.tsv6_service}: {e}")
            return False
        except OSError as e:
            self.logger.error(f"OS error starting {self.tsv6_service}: {e}")
            return False
    

    def _show_sleep_screen(self):
        """Display graphical sleep screen"""
        try:
            # Initialize display manager if not already done
            if not self.display_manager:
                self.display_manager = DisplayManager()
                self.logger.info("Display manager initialized for sleep screen")

            wake_time_str = self._get_wake_time_display()
            self.logger.info(f"Displaying sleep screen: Waking at {wake_time_str}")
            self.display_manager.show_sleep_screen(wake_time_str)
        except Exception as e:
            self.logger.error(f"Error showing sleep screen: {e}")
    
    def _close_display(self):
        """Close display manager"""
        if self.display_manager:
            try:
                self.display_manager.close()
                self.display_manager = None
                self.logger.info("Display manager closed")
            except Exception as e:
                self.logger.error(f"Error closing display: {e}")
    
    def _publish_sleep_status(self):
        """Publish sleep status to AWS using standard status format"""
        if not AWS_AVAILABLE or not self.sleep_config.publish_status_on_sleep:
            return

        try:
            aws_config = self.config_manager.get_aws_config()
            retry_config = RetryConfig(
                initial_delay=1.0,
                max_delay=10.0,
                max_attempts=3
            )

            self.aws_manager = ResilientAWSManager(
                endpoint=aws_config["endpoint"],
                cert_path=str(aws_config["cert_path"]),
                key_path=str(aws_config["key_path"]),
                ca_path=str(aws_config["ca_path"]),
                thing_name=aws_config["thing_name"],
                retry_config=retry_config
            )

            if self.aws_manager.connect():
                self.logger.info("Connected to AWS IoT")

                # Get system info for standard status format
                wifi_ssid, wifi_strength = self._get_wifi_info()
                cpu_temp = self._get_cpu_temperature()

                # Standard status payload with connectionState = "sleeping"
                status_payload = {
                    "thingName": aws_config["thing_name"],
                    "deviceType": "raspberry-pi",
                    "firmwareVersion": get_firmware_version(),
                    "wifiSSID": wifi_ssid,
                    "wifiStrength": wifi_strength,
                    "temperature": cpu_temp,
                    "timestampISO": datetime.datetime.utcnow().isoformat() + "Z",
                    "timeConnectedMins": 0,
                    "connectionState": "sleeping"
                }

                # Publish to shadow update topic
                shadow_topic = f"$aws/things/{aws_config['thing_name']}/shadow/update"
                shadow_payload = {
                    "state": {
                        "reported": status_payload
                    }
                }

                self.aws_manager.publish_with_retry(shadow_topic, shadow_payload)
                self.logger.info(f"Published sleep status to shadow: connectionState=sleeping")

                time.sleep(self.AWS_PUBLISH_DELAY_SECS)

                if self.sleep_config.disconnect_aws_on_sleep:
                    self.aws_manager.disconnect()
                    self.aws_manager = None
                    self.logger.info("Disconnected from AWS IoT")
            else:
                self.logger.warning("Failed to connect to AWS IoT for status publish")

        except Exception as e:
            self.logger.error(f"Error publishing sleep status: {e}")

    def _get_wifi_info(self) -> tuple:
        """Get WiFi SSID and signal strength"""
        try:
            env = os.environ.copy()
            env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:' + env.get('PATH', '')

            # Get SSID
            ssid = ""
            try:
                result = subprocess.run(["/usr/sbin/iwgetid", "-r"], capture_output=True, text=True, timeout=5, env=env)
                if result.returncode == 0:
                    ssid = result.stdout.strip()
            except Exception:
                pass

            # Get signal strength
            strength = 0
            try:
                result = subprocess.run(["/usr/sbin/iwconfig", "wlan0"], capture_output=True, text=True, timeout=5, env=env)
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if 'Signal level' in line:
                            import re
                            match = re.search(r'Signal level[=:](-?\d+)', line)
                            if match:
                                dbm = int(match.group(1))
                                # Convert dBm to percentage (rough estimate)
                                strength = max(0, min(100, 2 * (dbm + 100)))
            except Exception:
                pass

            return ssid, strength
        except Exception:
            return "", 0

    def _get_cpu_temperature(self) -> float:
        """Get CPU temperature"""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000.0
                return round(temp, 1)
        except Exception:
            return 0.0
    
    def _disconnect_aws(self):
        """Disconnect from AWS if connected"""
        if self.aws_manager:
            try:
                self.aws_manager.disconnect()
                self.aws_manager = None
                self.logger.info("AWS IoT disconnected")
            except Exception as e:
                self.logger.error(f"Error disconnecting AWS: {e}")
    
    def _enter_sleep(self):
        """Enter sleep mode"""
        self.logger.info("Entering sleep mode...")
        self.sleeping = True

        self._stop_tsv6_service()
        time.sleep(self.SERVICE_TRANSITION_DELAY_SECS)

        self._show_sleep_screen()

        self._publish_sleep_status()

        self.logger.info("Device is now sleeping")
    
    def _wake_up(self):
        """Wake from sleep mode"""
        self.logger.info("Waking up from sleep mode...")
        self.sleeping = False
        
        self._disconnect_aws()
        self._close_display()
        
        self._start_tsv6_service()
        
        self.logger.info("Device is now awake")
    
    def run(self):
        """Main service loop"""
        self.logger.info("Sleep service starting...")
        
        if not self.sleep_config.enabled:
            self.logger.info("Sleep mode is disabled in configuration")
            while self.running:
                time.sleep(self.ERROR_RECOVERY_INTERVAL_SECS)
            return

        while self.running:
            try:
                should_sleep = self._is_sleep_time()

                if should_sleep and not self.sleeping:
                    self._enter_sleep()
                elif not should_sleep and self.sleeping:
                    self._wake_up()

                time.sleep(self.MAIN_LOOP_INTERVAL_SECS)

            except Exception as e:
                self.logger.error(f"Error in sleep service loop: {e}")
                time.sleep(self.ERROR_RECOVERY_INTERVAL_SECS)
        
        if self.sleeping:
            self._wake_up()
        
        self.logger.info("Sleep service stopped")


def main():
    """Main entry point"""
    service = SleepService()
    service.run()


if __name__ == "__main__":
    main()
