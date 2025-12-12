#!/usr/bin/env python3
"""
WiFi Provisioner for TSV6
=========================

Handles first-boot WiFi provisioning via hotspot and captive portal.
Runs as a systemd service before the main TSV6 application.

Flow:
1. Check if WiFi is already configured and working
2. If not, start hotspot with web form for credential entry
3. Wait for user to submit credentials (with timeout)
4. Apply credentials and test connection
5. Exit to allow main application to start
"""

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Callable

from flask import Flask, request, render_template_string

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ProvisioningResult(Enum):
    """Result of provisioning attempt"""
    SUCCESS = "success"
    TIMEOUT = "timeout"
    ALREADY_CONFIGURED = "already_configured"
    CONNECTION_FAILED = "connection_failed"
    ERROR = "error"


@dataclass
class ProvisioningConfig:
    """Configuration for WiFi provisioning"""
    enabled: bool = True
    timeout_seconds: int = 600  # 10 minutes
    connection_test_timeout: int = 30
    max_connection_retries: int = 3

    # AP settings
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


class WiFiProvisioner:
    """
    Manages WiFi provisioning for first-boot setup.

    Creates a hotspot with captive portal for users to enter WiFi credentials.
    """

    def __init__(self, config: Optional[ProvisioningConfig] = None):
        self.config = config or ProvisioningConfig()
        self.device_id = self._get_device_id()
        self.ap_ssid = f"{self.config.ap_ssid_prefix}{self.device_id}"

        # Flask app for web form
        self.app = Flask(__name__)
        self._setup_routes()

        # State
        self.credentials_received = threading.Event()
        self.wifi_credentials: Optional[dict] = None
        self.server_thread: Optional[threading.Thread] = None
        self.shutdown_flag = threading.Event()

        # Signal handling
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _get_device_id(self) -> str:
        """Get unique device ID from Raspberry Pi serial number"""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        serial = line.split(':')[1].strip()
                        return serial[-8:].upper()
        except Exception as e:
            logger.warning(f"Could not read device serial: {e}")
        return "UNKNOWN"

    def _handle_signal(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, initiating shutdown")
        self.shutdown_flag.set()
        self.credentials_received.set()

    def _setup_routes(self):
        """Configure Flask routes for captive portal"""

        @self.app.route('/')
        def index():
            return render_template_string(self._get_html_template())

        @self.app.route('/configure', methods=['POST'])
        def configure():
            ssid = request.form.get('ssid', '').strip()
            password = request.form.get('password', '')

            if ssid:
                self.wifi_credentials = {'ssid': ssid, 'password': password}
                logger.info(f"Received WiFi credentials for SSID: {ssid}")
                self.credentials_received.set()
                return render_template_string(self._get_success_template())

            return render_template_string(self._get_html_template(error="Please enter a WiFi network name"))

        @self.app.route('/status')
        def status():
            return {'status': 'provisioning', 'device_id': self.device_id}

        # Catch-all for captive portal detection
        @self.app.route('/<path:path>')
        def catch_all(path):
            return render_template_string(self._get_html_template())

    def _get_html_template(self, error: str = "") -> str:
        """HTML template for WiFi configuration form"""
        error_html = f'<p class="error">{error}</p>' if error else ''
        return f'''
<!DOCTYPE html>
<html>
<head>
    <title>TSV6 WiFi Setup</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        .logo {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .logo h1 {{
            color: #1a1a2e;
            font-size: 24px;
            margin-bottom: 8px;
        }}
        .logo p {{
            color: #666;
            font-size: 14px;
        }}
        .device-id {{
            background: #f5f5f5;
            padding: 8px 12px;
            border-radius: 6px;
            font-family: monospace;
            font-size: 12px;
            color: #888;
            margin-top: 10px;
        }}
        input {{
            width: 100%;
            padding: 14px 16px;
            margin: 10px 0;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.2s;
        }}
        input:focus {{
            border-color: #007cba;
            outline: none;
        }}
        button {{
            width: 100%;
            padding: 16px;
            background: #007cba;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 20px;
            transition: background 0.2s;
        }}
        button:hover {{
            background: #005a87;
        }}
        .help {{
            text-align: center;
            margin-top: 20px;
            color: #888;
            font-size: 13px;
            line-height: 1.5;
        }}
        .error {{
            background: #fee;
            color: #c00;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 15px;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <h1>WiFi Setup</h1>
            <p>Connect your device to the internet</p>
            <div class="device-id">Device: {self.device_id}</div>
        </div>
        {error_html}
        <form method="post" action="/configure">
            <input type="text" name="ssid" placeholder="WiFi Network Name" required autocomplete="off">
            <input type="password" name="password" placeholder="WiFi Password" autocomplete="off">
            <button type="submit">Connect</button>
        </form>
        <p class="help">
            Enter your WiFi credentials to connect this device to your network.
            The device will restart automatically after connecting.
        </p>
    </div>
</body>
</html>
'''

    def _get_success_template(self) -> str:
        """HTML template for success message"""
        return '''
<!DOCTYPE html>
<html>
<head>
    <title>WiFi Configured</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 400px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        .checkmark {
            width: 80px;
            height: 80px;
            background: #4CAF50;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 20px;
            font-size: 40px;
            color: white;
        }
        h1 { color: #1a1a2e; margin-bottom: 15px; }
        p { color: #666; line-height: 1.6; }
    </style>
</head>
<body>
    <div class="container">
        <div class="checkmark">&#10003;</div>
        <h1>WiFi Configured!</h1>
        <p>Your device is now connecting to the WiFi network.</p>
        <p style="margin-top: 15px; font-size: 14px; color: #888;">
            You can close this page. The device will be ready shortly.
        </p>
    </div>
</body>
</html>
'''

    def needs_provisioning(self) -> bool:
        """
        Check if WiFi provisioning is needed.

        Returns True if:
        - No wpa_supplicant.conf exists
        - Config exists but has no network blocks
        - Config exists but can't connect
        """
        if not self.config.enabled:
            logger.info("Provisioning disabled in config")
            return False

        # Check if config file exists
        if not os.path.exists(self.config.wpa_supplicant_conf):
            logger.info("No wpa_supplicant.conf found - provisioning needed")
            return True

        # Check if config has network blocks
        if not self._has_network_config():
            logger.info("wpa_supplicant.conf has no network blocks - provisioning needed")
            return True

        # Try to connect with existing config
        if not self._can_connect():
            logger.info("Cannot connect with existing config - provisioning needed")
            return True

        logger.info("WiFi is configured and working - no provisioning needed")
        return False

    def _has_network_config(self) -> bool:
        """Check if wpa_supplicant.conf contains network configuration"""
        try:
            with open(self.config.wpa_supplicant_conf, 'r') as f:
                content = f.read()
                # Look for network={} blocks
                return bool(re.search(r'network\s*=\s*\{', content))
        except Exception as e:
            logger.error(f"Error reading wpa_supplicant.conf: {e}")
            return False

    def _can_connect(self, timeout: int = 30) -> bool:
        """
        Test if existing WiFi config can establish connection.
        Returns True if connected and has internet access.
        """
        logger.info(f"Testing WiFi connection (timeout: {timeout}s)")

        start_time = time.time()
        while time.time() - start_time < timeout:
            # Check if interface has an IP address (not AP range)
            try:
                result = subprocess.run(
                    ['ip', 'addr', 'show', self.config.ap_interface],
                    capture_output=True, text=True, timeout=5
                )

                # Look for an IP address that's not in the AP range
                ip_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', result.stdout)
                if ip_match:
                    ip = ip_match.group(1)
                    if not ip.startswith('192.168.4.'):  # Not AP address
                        # Test internet connectivity
                        if self._test_internet():
                            logger.info(f"WiFi connected with IP: {ip}")
                            return True
            except Exception as e:
                logger.debug(f"Connection check error: {e}")

            time.sleep(2)

        logger.warning("WiFi connection test failed")
        return False

    def _test_internet(self, host: str = "8.8.8.8", timeout: int = 5) -> bool:
        """Test internet connectivity with ping"""
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', str(timeout), host],
                capture_output=True, timeout=timeout + 2
            )
            return result.returncode == 0
        except Exception:
            return False

    def _create_hostapd_config(self) -> bool:
        """Create hostapd configuration file"""
        config = f"""interface={self.config.ap_interface}
driver=nl80211
ssid={self.ap_ssid}
hw_mode=g
channel={self.config.ap_channel}
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={self.config.ap_password}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
"""
        try:
            with open(self.config.hostapd_conf, 'w') as f:
                f.write(config)
            logger.info(f"Created hostapd config: {self.config.hostapd_conf}")
            return True
        except Exception as e:
            logger.error(f"Failed to create hostapd config: {e}")
            return False

    def _create_dnsmasq_config(self) -> bool:
        """Create dnsmasq configuration file for DHCP and DNS redirect"""
        config = f"""interface={self.config.ap_interface}
dhcp-range={self.config.ap_dhcp_start},{self.config.ap_dhcp_end},{self.config.ap_netmask},24h
address=/#/{self.config.ap_ip}
"""
        try:
            with open(self.config.dnsmasq_conf, 'w') as f:
                f.write(config)
            logger.info(f"Created dnsmasq config: {self.config.dnsmasq_conf}")
            return True
        except Exception as e:
            logger.error(f"Failed to create dnsmasq config: {e}")
            return False

    def _start_access_point(self) -> bool:
        """Start WiFi access point with hostapd and dnsmasq"""
        logger.info(f"Starting access point: {self.ap_ssid}")

        try:
            # Stop any existing services
            subprocess.run(['systemctl', 'stop', 'hostapd'], capture_output=True)
            subprocess.run(['systemctl', 'stop', 'dnsmasq'], capture_output=True)
            subprocess.run(['killall', 'wpa_supplicant'], capture_output=True)
            subprocess.run(['killall', 'hostapd'], capture_output=True)
            subprocess.run(['killall', 'dnsmasq'], capture_output=True)
            time.sleep(1)

            # Create config files
            if not self._create_hostapd_config():
                return False
            if not self._create_dnsmasq_config():
                return False

            # Configure interface
            subprocess.run(['ip', 'link', 'set', self.config.ap_interface, 'down'], check=True)
            subprocess.run(['ip', 'addr', 'flush', 'dev', self.config.ap_interface], check=True)
            subprocess.run([
                'ip', 'addr', 'add',
                f'{self.config.ap_ip}/24',
                'dev', self.config.ap_interface
            ], check=True)
            subprocess.run(['ip', 'link', 'set', self.config.ap_interface, 'up'], check=True)
            time.sleep(1)

            # Start dnsmasq
            dnsmasq_proc = subprocess.Popen(
                ['dnsmasq', '-C', self.config.dnsmasq_conf, '-d'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.info(f"Started dnsmasq (PID: {dnsmasq_proc.pid})")

            # Start hostapd
            hostapd_proc = subprocess.Popen(
                ['hostapd', self.config.hostapd_conf],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.info(f"Started hostapd (PID: {hostapd_proc.pid})")

            time.sleep(3)  # Wait for services to stabilize

            # Verify hostapd is running
            if hostapd_proc.poll() is not None:
                logger.error("hostapd failed to start")
                return False

            logger.info(f"Access point '{self.ap_ssid}' started successfully")
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start access point: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error starting access point: {e}")
            return False

    def _stop_access_point(self):
        """Stop the access point and cleanup"""
        logger.info("Stopping access point")

        try:
            subprocess.run(['killall', 'hostapd'], capture_output=True)
            subprocess.run(['killall', 'dnsmasq'], capture_output=True)

            # Clean up config files
            for conf_file in [self.config.hostapd_conf, self.config.dnsmasq_conf]:
                if os.path.exists(conf_file):
                    os.remove(conf_file)

            # Reset interface
            subprocess.run(['ip', 'addr', 'flush', 'dev', self.config.ap_interface], capture_output=True)
            subprocess.run(['ip', 'link', 'set', self.config.ap_interface, 'down'], capture_output=True)
            subprocess.run(['ip', 'link', 'set', self.config.ap_interface, 'up'], capture_output=True)

        except Exception as e:
            logger.error(f"Error stopping access point: {e}")

    def _start_web_server(self):
        """Start Flask web server in background thread"""
        def run_server():
            # Suppress Flask logging
            import logging as flask_logging
            flask_logging.getLogger('werkzeug').setLevel(flask_logging.ERROR)

            try:
                self.app.run(
                    host='0.0.0.0',
                    port=self.config.web_port,
                    debug=False,
                    use_reloader=False,
                    threaded=True
                )
            except Exception as e:
                logger.error(f"Web server error: {e}")

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        logger.info(f"Web server started on port {self.config.web_port}")

    def _apply_wifi_config(self, ssid: str, password: str) -> bool:
        """Apply WiFi credentials and test connection"""
        logger.info(f"Applying WiFi config for SSID: {ssid}")

        # Stop access point first
        self._stop_access_point()
        time.sleep(2)

        # Create wpa_supplicant config
        wpa_config = f'''ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

network={{
    ssid="{ssid}"
    psk="{password}"
    key_mgmt=WPA-PSK
}}
'''

        try:
            # Backup existing config if present
            if os.path.exists(self.config.wpa_supplicant_conf):
                backup_path = f"{self.config.wpa_supplicant_conf}.backup"
                subprocess.run(['cp', self.config.wpa_supplicant_conf, backup_path], check=True)
                logger.info(f"Backed up existing config to {backup_path}")

            # Write new config to temp file first
            temp_conf = '/tmp/wpa_supplicant_new.conf'
            with open(temp_conf, 'w') as f:
                f.write(wpa_config)

            # Copy to system location
            subprocess.run(['cp', temp_conf, self.config.wpa_supplicant_conf], check=True)
            subprocess.run(['chmod', '600', self.config.wpa_supplicant_conf], check=True)
            os.remove(temp_conf)

            logger.info("WiFi configuration saved")

            # Restart networking
            subprocess.run(['systemctl', 'restart', 'dhcpcd'], capture_output=True)
            subprocess.run(['wpa_cli', '-i', self.config.ap_interface, 'reconfigure'], capture_output=True)

            # Test connection
            logger.info("Testing new WiFi connection...")
            if self._can_connect(timeout=self.config.connection_test_timeout):
                logger.info("WiFi connection successful!")
                return True
            else:
                logger.warning("WiFi connection test failed")
                return False

        except Exception as e:
            logger.error(f"Failed to apply WiFi config: {e}")
            return False

    def start_provisioning(self, timeout: Optional[int] = None) -> ProvisioningResult:
        """
        Start the provisioning process.

        Args:
            timeout: Timeout in seconds (default from config)

        Returns:
            ProvisioningResult indicating outcome
        """
        timeout = timeout or self.config.timeout_seconds
        logger.info(f"Starting WiFi provisioning (timeout: {timeout}s)")

        # Start access point
        if not self._start_access_point():
            logger.error("Failed to start access point")
            return ProvisioningResult.ERROR

        # Start web server
        self._start_web_server()

        # Print connection info
        print("\n" + "=" * 50)
        print("WiFi Provisioning Mode Active")
        print("=" * 50)
        print(f"Connect to WiFi: {self.ap_ssid}")
        print(f"Password: {self.config.ap_password}")
        print(f"Then open: http://{self.config.ap_ip}")
        print(f"Timeout: {timeout // 60} minutes")
        print("=" * 50 + "\n")

        # Wait for credentials or timeout
        logger.info(f"Waiting for credentials (timeout: {timeout}s)")
        received = self.credentials_received.wait(timeout=timeout)

        if self.shutdown_flag.is_set():
            logger.info("Shutdown requested")
            self._stop_access_point()
            return ProvisioningResult.ERROR

        if not received:
            logger.warning("Provisioning timed out")
            self._stop_access_point()
            return ProvisioningResult.TIMEOUT

        # Apply credentials
        if self.wifi_credentials:
            ssid = self.wifi_credentials['ssid']
            password = self.wifi_credentials['password']

            for attempt in range(self.config.max_connection_retries):
                logger.info(f"Connection attempt {attempt + 1}/{self.config.max_connection_retries}")

                if self._apply_wifi_config(ssid, password):
                    return ProvisioningResult.SUCCESS

                time.sleep(2)

            logger.error("All connection attempts failed")
            return ProvisioningResult.CONNECTION_FAILED

        return ProvisioningResult.ERROR

    def run(self) -> int:
        """
        Main entry point for systemd service.

        Returns:
            Exit code (0 for success/timeout, 1 for error)
        """
        logger.info("WiFi Provisioner starting")

        try:
            # Check if provisioning is needed
            if not self.needs_provisioning():
                logger.info("WiFi already configured - exiting")
                return 0

            # Run provisioning
            result = self.start_provisioning()

            if result == ProvisioningResult.SUCCESS:
                logger.info("Provisioning completed successfully")
                return 0
            elif result == ProvisioningResult.TIMEOUT:
                logger.info("Provisioning timed out - continuing boot")
                return 0  # Exit 0 to allow main app to start
            elif result == ProvisioningResult.ALREADY_CONFIGURED:
                logger.info("WiFi already configured")
                return 0
            else:
                logger.error(f"Provisioning failed: {result}")
                return 0  # Still exit 0 to not block boot

        except Exception as e:
            logger.exception(f"Unexpected error in provisioner: {e}")
            return 1


def main():
    """Entry point when run as module"""
    provisioner = WiFiProvisioner()
    sys.exit(provisioner.run())


if __name__ == "__main__":
    main()
