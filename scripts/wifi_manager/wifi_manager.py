#!/usr/bin/env python3
"""
Simple WiFi Manager for Raspberry Pi
Creates a hotspot, displays QR code, and provides captive portal for WiFi configuration
"""

import os
import sys
import time
import subprocess
import threading
import qrcode
from flask import Flask, request, render_template_string, redirect, url_for, jsonify
import socket

class WiFiManager:
    def __init__(self, ssid="RPi-Setup", password="setupwifi", interface="wlan0"):
        self.ssid = ssid
        self.password = password
        self.interface = interface
        self.app = Flask(__name__)
        self.hotspot_ip = "192.168.4.1"
        self.setup_routes()
        self.wifi_credentials = None
        
    def setup_routes(self):
        @self.app.route('/')
        def index():
            return render_template_string(self.get_html_template())
        
        @self.app.route('/configure', methods=['POST'])
        def configure():
            ssid = request.form.get('ssid')
            password = request.form.get('password')
            
            if ssid:
                self.wifi_credentials = {'ssid': ssid, 'password': password}
                print(f"Received WiFi credentials: {ssid}")
                
                # Save credentials and restart in background
                threading.Thread(target=self.apply_wifi_config, daemon=True).start()
                
                return render_template_string("""
                <html><head><title>WiFi Configuration</title></head>
                <body style="font-family: Arial; text-align: center; margin-top: 50px;">
                <h2>WiFi Configuration Saved!</h2>
                <p>Your Raspberry Pi will now restart and connect to the WiFi network.</p>
                <p>This may take a few minutes...</p>
                </body></html>
                """)
            
            return redirect(url_for('index'))
    
    def get_html_template(self):
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>WiFi Setup</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: Arial; text-align: center; margin: 20px; background: #f0f0f0; }
                .container { max-width: 400px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                h1 { color: #333; margin-bottom: 30px; }
                input { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
                button { width: 100%; padding: 15px; background: #007cba; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }
                button:hover { background: #005a87; }
                .note { margin-top: 20px; font-size: 14px; color: #666; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🛜 WiFi Setup</h1>
                <form method="post" action="/configure">
                    <input type="text" name="ssid" placeholder="WiFi Network Name (SSID)" required>
                    <input type="password" name="password" placeholder="WiFi Password">
                    <button type="submit">Connect to WiFi</button>
                </form>
                <div class="note">
                    <p>Enter your WiFi credentials to connect this device to your network.</p>
                </div>
            </div>
        </body>
        </html>
        """
    
    def generate_qr_code(self):
        """Generate and display QR code for WiFi connection"""
        wifi_qr_data = f"WIFI:T:WPA;S:{self.ssid};P:{self.password};;"
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(wifi_qr_data)
        qr.make(fit=True)
        
        # Create ASCII QR code for terminal display
        qr_ascii = qrcode.QRCode(version=1, border=2)
        qr_ascii.add_data(wifi_qr_data)
        qr_ascii.make(fit=True)
        
        print("\n" + "="*60)
        print("WiFi Manager - Setup Mode")
        print("="*60)
        print(f"Hotspot SSID: {self.ssid}")
        print(f"Hotspot Password: {self.password}")
        print(f"Setup URL: http://{self.hotspot_ip}")
        print("\nScan this QR code to connect:")
        print("-"*40)
        
        qr_ascii.print_ascii(invert=True)
        
        print("-"*40)
        print("Or manually connect to the hotspot and visit:")
        print(f"http://{self.hotspot_ip}")
        print("="*60)
    
    def create_hostapd_config(self):
        """Create hostapd configuration"""
        config = f"""
interface={self.interface}
driver=nl80211
ssid={self.ssid}
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={self.password}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
"""
        
        with open('/tmp/hostapd.conf', 'w') as f:
            f.write(config.strip())
    
    def create_dnsmasq_config(self):
        """Create dnsmasq configuration"""
        config = f"""
interface={self.interface}
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/#/{self.hotspot_ip}
"""
        
        with open('/tmp/dnsmasq.conf', 'w') as f:
            f.write(config.strip())
    
    def start_hotspot(self):
        """Start the WiFi hotspot"""
        print("Starting WiFi hotspot...")
        
        try:
            # Stop any existing services
            subprocess.run(['sudo', 'systemctl', 'stop', 'hostapd'], capture_output=True)
            subprocess.run(['sudo', 'systemctl', 'stop', 'dnsmasq'], capture_output=True)
            subprocess.run(['sudo', 'killall', 'wpa_supplicant'], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Create configuration files
            self.create_hostapd_config()
            self.create_dnsmasq_config()
            
            # Configure interface
            subprocess.run(['sudo', 'ip', 'link', 'set', self.interface, 'down'])
            subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', self.interface])
            subprocess.run(['sudo', 'ip', 'addr', 'add', f'{self.hotspot_ip}/24', 'dev', self.interface])
            subprocess.run(['sudo', 'ip', 'link', 'set', self.interface, 'up'])
            
            # Start dnsmasq
            try:
                subprocess.run(['sudo', 'dnsmasq', '-C', '/tmp/dnsmasq.conf', '-d'], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, 
                             timeout=2)
            except subprocess.TimeoutExpired:
                pass  # Expected timeout as dnsmasq runs in background
            
            # Start hostapd in background
            subprocess.Popen(['sudo', 'hostapd', '/tmp/hostapd.conf'], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            time.sleep(3)  # Wait for services to start
            print("Hotspot started successfully!")
            return True
            
        except Exception as e:
            print(f"Error starting hotspot: {e}")
            return False
    
    def apply_wifi_config(self):
        """Apply WiFi configuration and restart"""
        if not self.wifi_credentials:
            return
            
        print(f"Applying WiFi configuration for: {self.wifi_credentials['ssid']}")
        
        # Stop hotspot services
        subprocess.run(['sudo', 'killall', 'hostapd'], 
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['sudo', 'killall', 'dnsmasq'], 
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Save WiFi configuration
        wpa_config = f"""
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

network={{
    ssid="{self.wifi_credentials['ssid']}"
    psk="{self.wifi_credentials['password']}"
    key_mgmt=WPA-PSK
}}
"""
        
        try:
            # Backup existing config
            subprocess.run(['sudo', 'cp', '/etc/wpa_supplicant/wpa_supplicant.conf', 
                          '/etc/wpa_supplicant/wpa_supplicant.conf.backup'], 
                         capture_output=True)
            
            # Write new config
            with open('/tmp/wpa_supplicant.conf', 'w') as f:
                f.write(wpa_config.strip())
            
            subprocess.run(['sudo', 'cp', '/tmp/wpa_supplicant.conf', 
                          '/etc/wpa_supplicant/wpa_supplicant.conf'])
            
            print("WiFi configuration saved. Restarting...")
            time.sleep(2)
            
            # Restart networking
            subprocess.run(['sudo', 'systemctl', 'restart', 'dhcpcd'])
            subprocess.run(['sudo', 'wpa_cli', '-i', self.interface, 'reconfigure'])
            
        except Exception as e:
            print(f"Error applying WiFi config: {e}")
    
    def run(self):
        """Main function to start WiFi manager"""
        print("Starting WiFi Manager...")
        
        if self.start_hotspot():
            self.generate_qr_code()
            
            # Start web server
            try:
                self.app.run(host='0.0.0.0', port=80, debug=False)
            except PermissionError:
                print("Need root permissions to run on port 80. Trying port 8080...")
                self.app.run(host='0.0.0.0', port=8080, debug=False)
        else:
            print("Failed to start hotspot!")
            return False

# Main execution
if __name__ == "__main__":
    # Check if running as root
    if os.geteuid() != 0:
        print("This script needs to be run with sudo for network configuration")
        print("Usage: sudo python3 wifi_manager.py")
        sys.exit(1)
    
    manager = WiFiManager()
    try:
        manager.run()
    except KeyboardInterrupt:
        print("\nWiFi Manager stopped by user")
    except Exception as e:
        print(f"Error: {e}")
