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
import hashlib
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

    def __init__(self, config: Optional[ProvisioningConfig] = None,
                 on_status_update: Optional[Callable[[str, Optional[dict]], None]] = None):
        self.config = config or ProvisioningConfig()
        self.device_id = self._get_device_id()
        self.ap_ssid = f"{self.config.ap_ssid_prefix}{self.device_id}"
        self.on_status_update = on_status_update

        # Flask app for web form
        self.app = Flask(__name__)
        self._setup_routes()

        # State
        self.credentials_received = threading.Event()
        self.wifi_credentials: Optional[dict] = None
        self.server_thread: Optional[threading.Thread] = None
        self.shutdown_flag = threading.Event()
        self.cached_networks: list = []  # Cache networks before AP starts

        # Signal handling
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _notify_status(self, status: str, details: Optional[dict] = None):
        """Notify status update to callback if registered"""
        if self.on_status_update:
            try:
                self.on_status_update(status, details)
            except Exception as e:
                logger.warning(f"Status callback error: {e}")

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

    def _scan_wifi_networks(self, use_cache: bool = True) -> list:
        """Scan for available WiFi networks. Returns cached results if in AP mode."""
        # Return cached networks if available (can't scan while in AP mode)
        if use_cache and self.cached_networks:
            logger.debug(f"Returning {len(self.cached_networks)} cached networks")
            return self.cached_networks

        networks = []
        try:
            # Use iwlist to scan (need to temporarily bring up a scan interface)
            result = subprocess.run(
                ['/usr/sbin/iwlist', 'wlan0', 'scan'],
                capture_output=True,
                text=True,
                timeout=15
            )

            if result.returncode == 0:
                current_network = {}
                for line in result.stdout.split('\n'):
                    line = line.strip()
                    if 'ESSID:' in line:
                        ssid = line.split('ESSID:')[1].strip('"')
                        if ssid and ssid != self.ap_ssid:  # Don't show our own AP
                            current_network['ssid'] = ssid
                    elif 'Signal level=' in line:
                        try:
                            # Extract signal level (dBm)
                            signal_part = line.split('Signal level=')[1].split()[0]
                            current_network['signal'] = int(signal_part.replace('dBm', ''))
                        except:
                            current_network['signal'] = -100
                    elif 'Encryption key:' in line:
                        current_network['encrypted'] = 'on' in line.lower()

                    # When we have a complete network entry, add it
                    if 'ssid' in current_network and current_network['ssid']:
                        if current_network not in networks:
                            networks.append(current_network.copy())
                        current_network = {}

                # Remove duplicates and sort by signal strength
                seen = set()
                unique_networks = []
                for net in networks:
                    if net['ssid'] not in seen:
                        seen.add(net['ssid'])
                        unique_networks.append(net)

                # Sort by signal strength (strongest first)
                unique_networks.sort(key=lambda x: x.get('signal', -100), reverse=True)
                networks = unique_networks[:15]  # Limit to top 15

        except subprocess.TimeoutExpired:
            logger.warning("WiFi scan timed out")
        except Exception as e:
            logger.error(f"Error scanning WiFi networks: {e}")

        return networks

    def _get_redirect_template(self) -> str:
        """HTML template for captive portal redirect"""
        return '''
<!DOCTYPE html>
<html>
<head>
    <title>Redirecting...</title>
    <meta http-equiv="refresh" content="0;url=http://192.168.4.1/">
    <script>window.location.href = "http://192.168.4.1/";</script>
</head>
<body>
    <p>Redirecting to WiFi setup...</p>
    <p><a href="http://192.168.4.1/">Click here if not redirected</a></p>
</body>
</html>
'''

    def _handle_signal(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, initiating shutdown")
        self.shutdown_flag.set()
        self.credentials_received.set()

    def _setup_routes(self):
        """Configure Flask routes for captive portal"""

        @self.app.route('/')
        def index():
            networks = self._scan_wifi_networks()
            return render_template_string(self._get_html_template(networks=networks))

        @self.app.route('/configure', methods=['POST'])
        def configure():
            ssid = request.form.get('ssid', '').strip()
            password = request.form.get('password', '')

            if ssid:
                self.wifi_credentials = {'ssid': ssid, 'password': password}
                # Never log plaintext passwords; log metadata + stable hash for correlation.
                logger.info(
                    "Received WiFi credentials: ssid=%r pw_meta=%s",
                    ssid,
                    self._password_meta(password),
                )
                self.credentials_received.set()
                return render_template_string(self._get_success_template())

            networks = self._scan_wifi_networks()
            return render_template_string(self._get_html_template(networks=networks, error="Please select a WiFi network"))

        @self.app.route('/status')
        def status():
            return {'status': 'provisioning', 'device_id': self.device_id}

        @self.app.route('/networks')
        def networks():
            """API endpoint to get available networks"""
            return {'networks': self._scan_wifi_networks()}

        # Captive portal detection endpoints - redirect to main page
        # Android
        @self.app.route('/generate_204')
        @self.app.route('/gen_204')
        def android_captive():
            return render_template_string(self._get_redirect_template())

        # iOS/macOS
        @self.app.route('/hotspot-detect.html')
        @self.app.route('/library/test/success.html')
        def apple_captive():
            return render_template_string(self._get_redirect_template())

        # Windows
        @self.app.route('/connecttest.txt')
        @self.app.route('/ncsi.txt')
        def windows_captive():
            return render_template_string(self._get_redirect_template())

        # Catch-all for captive portal detection
        @self.app.route('/<path:path>')
        def catch_all(path):
            networks = self._scan_wifi_networks()
            return render_template_string(self._get_html_template(networks=networks))

    def _get_html_template(self, error: str = "", networks: list = None) -> str:
        """HTML template for WiFi configuration form with network list"""
        error_html = f'<p class="error">{error}</p>' if error else ''
        networks = networks or []

        # Build network list HTML
        if networks:
            network_options = ''
            for net in networks:
                signal = net.get('signal', -100)
                # Convert signal to bars (1-4)
                if signal > -50:
                    bars = 4
                elif signal > -60:
                    bars = 3
                elif signal > -70:
                    bars = 2
                else:
                    bars = 1
                signal_icon = '▂' * bars + '▂' * (4 - bars)
                lock_icon = '🔒' if net.get('encrypted', True) else ''
                network_options += f'''
                <div class="network-item" onclick="selectNetwork('{net['ssid']}')">
                    <span class="network-name">{net['ssid']}</span>
                    <span class="network-info">{lock_icon} <span class="signal">{signal_icon}</span></span>
                </div>'''
            network_list_html = f'<div class="network-list">{network_options}</div>'
        else:
            network_list_html = '<p class="no-networks">Scanning for networks...</p>'

        return f'''
<!DOCTYPE html>
<html>
<head>
    <title>Topper Stopper WiFi Setup</title>
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
            padding: 30px;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        .logo {{
            text-align: center;
            margin-bottom: 20px;
        }}
        .logo h1 {{
            color: #1a1a2e;
            font-size: 22px;
            margin-bottom: 5px;
        }}
        .logo p {{
            color: #666;
            font-size: 14px;
        }}
        .network-list {{
            max-height: 250px;
            overflow-y: auto;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            margin-bottom: 15px;
        }}
        .network-item {{
            padding: 14px 16px;
            border-bottom: 1px solid #f0f0f0;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background 0.2s;
        }}
        .network-item:last-child {{
            border-bottom: none;
        }}
        .network-item:hover {{
            background: #f5f5f5;
        }}
        .network-item.selected {{
            background: #e3f2fd;
            border-left: 3px solid #007cba;
        }}
        .network-name {{
            font-size: 16px;
            color: #333;
        }}
        .network-info {{
            font-size: 14px;
            color: #888;
        }}
        .signal {{
            font-family: monospace;
            color: #4CAF50;
        }}
        .no-networks {{
            text-align: center;
            padding: 20px;
            color: #888;
        }}
        .password-section {{
            display: none;
            margin-top: 15px;
        }}
        .password-section.visible {{
            display: block;
        }}
        .selected-network {{
            background: #f5f5f5;
            padding: 10px 15px;
            border-radius: 8px;
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .selected-network-name {{
            font-weight: 600;
            color: #333;
        }}
        .change-btn {{
            color: #007cba;
            background: none;
            border: none;
            cursor: pointer;
            font-size: 14px;
        }}
        input {{
            width: 100%;
            padding: 14px 16px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.2s;
        }}
        input:focus {{
            border-color: #007cba;
            outline: none;
        }}
        button[type="submit"] {{
            width: 100%;
            padding: 16px;
            background: #007cba;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 15px;
            transition: background 0.2s;
        }}
        button[type="submit"]:hover {{
            background: #005a87;
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
            <h1>Topper Stopper</h1>
            <p>Select your WiFi network</p>
        </div>
        {error_html}
        <form method="post" action="/configure" id="wifiForm">
            <input type="hidden" name="ssid" id="ssidInput" value="">

            <div id="networkSelection">
                {network_list_html}
            </div>

            <div class="password-section" id="passwordSection">
                <div class="selected-network">
                    <span class="selected-network-name" id="selectedNetworkName"></span>
                    <button type="button" class="change-btn" onclick="changeNetwork()">Change</button>
                </div>
                <input type="password" name="password" id="passwordInput" placeholder="Enter WiFi password" autocomplete="off">
                <button type="submit">Connect</button>
            </div>
        </form>
    </div>

    <script>
        function selectNetwork(ssid) {{
            document.getElementById('ssidInput').value = ssid;
            document.getElementById('selectedNetworkName').textContent = ssid;
            document.getElementById('networkSelection').style.display = 'none';
            document.getElementById('passwordSection').classList.add('visible');
            document.getElementById('passwordInput').focus();

            // Highlight selected item
            document.querySelectorAll('.network-item').forEach(item => {{
                item.classList.remove('selected');
                if (item.querySelector('.network-name').textContent === ssid) {{
                    item.classList.add('selected');
                }}
            }});
        }}

        function changeNetwork() {{
            document.getElementById('ssidInput').value = '';
            document.getElementById('networkSelection').style.display = 'block';
            document.getElementById('passwordSection').classList.remove('visible');
        }}
    </script>
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
        - Config exists but saved network is not visible (immediate broadcast)
        - Config exists, network visible, but can't connect
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

        # Check if saved network is visible before attempting connection
        # This enables immediate broadcast when saved network is not found
        if not self._is_saved_network_visible():
            logger.info("Saved network not visible - provisioning needed (immediate broadcast)")
            return True

        # Try to connect with existing config (only if saved network is visible)
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

    def _get_saved_ssids(self) -> list:
        """
        Extract saved SSIDs from wpa_supplicant.conf.

        Returns:
            List of saved SSID strings, or empty list if none found.
        """
        ssids = []
        try:
            with open(self.config.wpa_supplicant_conf, 'r') as f:
                content = f.read()
                # Match ssid="..." inside network blocks
                # Pattern handles both quoted SSIDs and hex SSIDs
                matches = re.findall(r'ssid="([^"]+)"', content)
                ssids = [m for m in matches if m]
                if ssids:
                    logger.debug(f"Found saved SSIDs: {ssids}")
        except Exception as e:
            logger.error(f"Error reading saved SSIDs: {e}")
        return ssids

    def _is_saved_network_visible(self) -> bool:
        """
        Check if any saved network SSID is visible in WiFi scan.

        This enables immediate broadcast when saved network is not found,
        rather than waiting for connection timeout.

        Returns:
            True if at least one saved SSID is visible, False otherwise.
        """
        saved_ssids = self._get_saved_ssids()
        if not saved_ssids:
            logger.info("No saved SSIDs found in config")
            return False

        logger.info(f"Scanning for saved networks: {saved_ssids}")
        available_networks = self._scan_wifi_networks(use_cache=False)
        available_ssids = {net.get('ssid', '') for net in available_networks}

        for ssid in saved_ssids:
            if ssid in available_ssids:
                logger.info(f"Saved network '{ssid}' is visible")
                return True

        logger.info(f"No saved networks visible. Available: {list(available_ssids)[:5]}")
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
wpa_pairwise=CCMP
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

        # Scan for networks BEFORE starting AP (can't scan in AP mode)
        logger.info("Scanning for WiFi networks before starting AP...")
        self.cached_networks = self._scan_wifi_networks(use_cache=False)
        logger.info(f"Cached {len(self.cached_networks)} networks for captive portal")

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

            # Start dnsmasq (use full path)
            dnsmasq_proc = subprocess.Popen(
                ['/usr/sbin/dnsmasq', '-C', self.config.dnsmasq_conf, '-d'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.info(f"Started dnsmasq (PID: {dnsmasq_proc.pid})")

            # Start hostapd (use full path)
            hostapd_proc = subprocess.Popen(
                ['/usr/sbin/hostapd', self.config.hostapd_conf],
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

    def _password_meta(self, password: str) -> dict:
        """Return safe-to-log metadata about a password (never plaintext)."""
        try:
            pw = password if password is not None else ""
            pw_bytes = pw.encode("utf-8", errors="replace")
            sha = hashlib.sha256(pw_bytes).hexdigest()[:12]
            is_hex64 = bool(re.fullmatch(r"[0-9a-fA-F]{64}", pw))
            return {
                "len": len(pw),
                "leading_ws": (len(pw) > 0 and pw[:1].isspace()),
                "trailing_ws": (len(pw) > 0 and pw[-1:].isspace()),
                "has_quote": ('"' in pw),
                "has_backslash": ('\\' in pw),
                "has_newline": ('\n' in pw or '\r' in pw),
                "hex64": is_hex64,
                "sha12": sha,
            }
        except Exception:
            return {"len": None, "sha12": None}

    def _run_capture(self, cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
        """Run command and capture stdout/stderr for debugging."""
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
        except Exception as e:
            return 1, "", f"{type(e).__name__}: {e}"

    def _apply_wifi_config(self, ssid: str, password: str) -> bool:
        """Apply WiFi credentials using NetworkManager and test connection"""
        logger.info("Applying WiFi config: ssid=%r pw_meta=%s", ssid, self._password_meta(password))

        # Stop access point first
        self._stop_access_point()
        time.sleep(2)

        try:
            # Re-enable WiFi in NetworkManager (it gets disabled when we use hostapd)
            rc, out, err = self._run_capture(['nmcli', 'radio', 'wifi', 'on'], timeout=10)
            logger.info("nmcli radio wifi on: rc=%s out=%r err=%r", rc, out, err)
            time.sleep(2)

            # Delete any existing connection with the same SSID to avoid conflicts
            rc, out, err = self._run_capture(
                ['nmcli', 'connection', 'delete', ssid],
                timeout=10
            )
            logger.info("nmcli delete existing connection: rc=%s out=%r err=%r", rc, out, err)

            # Connect to the WiFi network using nmcli
            # This creates a new connection profile and connects
            logger.info(f"Connecting to WiFi network: {ssid}")
            rc, out, err = self._run_capture(
                ['nmcli', 'device', 'wifi', 'connect', ssid, 'password', password, 'ifname', self.config.ap_interface],
                timeout=30
            )
            logger.info("nmcli wifi connect: rc=%s out=%r err=%r", rc, out, err)

            if rc != 0:
                logger.error(f"nmcli connect failed: {err}")
                # Try alternative: create connection then activate
                logger.info("Trying alternative connection method...")

                # Create connection profile
                rc, out, err = self._run_capture(
                    ['nmcli', 'connection', 'add', 'type', 'wifi', 'con-name', ssid,
                     'ssid', ssid, 'wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', password],
                    timeout=15
                )
                logger.info("nmcli connection add: rc=%s out=%r err=%r", rc, out, err)

                if rc == 0:
                    # Activate the connection
                    rc, out, err = self._run_capture(
                        ['nmcli', 'connection', 'up', ssid, 'ifname', self.config.ap_interface],
                        timeout=30
                    )
                    logger.info("nmcli connection up: rc=%s out=%r err=%r", rc, out, err)

            # Check connection status
            time.sleep(3)
            rc, out, err = self._run_capture(['nmcli', 'device', 'status'], timeout=10)
            logger.info("nmcli device status: rc=%s out=%r", rc, out)

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
        self._notify_status("starting_hotspot", {"ssid": self.ap_ssid})
        if not self._start_access_point():
            logger.error("Failed to start access point")
            self._notify_status("hotspot_failed")
            return ProvisioningResult.ERROR

        # Start web server
        self._notify_status("starting_portal", {"ip": self.config.ap_ip, "port": self.config.web_port})
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
        self._notify_status("waiting_for_credentials", {"timeout": timeout})
        received = self.credentials_received.wait(timeout=timeout)

        if self.shutdown_flag.is_set():
            logger.info("Shutdown requested")
            self._stop_access_point()
            self._notify_status("shutdown")
            return ProvisioningResult.ERROR

        if not received:
            logger.warning("Provisioning timed out")
            self._stop_access_point()
            self._notify_status("timeout")
            return ProvisioningResult.TIMEOUT

        # Apply credentials
        if self.wifi_credentials:
            ssid = self.wifi_credentials['ssid']
            password = self.wifi_credentials['password']

            for attempt in range(self.config.max_connection_retries):
                logger.info(f"Connection attempt {attempt + 1}/{self.config.max_connection_retries}")
                self._notify_status("connecting", {
                    "ssid": ssid,
                    "attempt": attempt + 1,
                    "max_attempts": self.config.max_connection_retries
                })

                if self._apply_wifi_config(ssid, password):
                    self._notify_status("connected", {"ssid": ssid})
                    return ProvisioningResult.SUCCESS

                time.sleep(2)

            logger.error("All connection attempts failed")
            self._notify_status("connection_failed", {"ssid": ssid})
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
