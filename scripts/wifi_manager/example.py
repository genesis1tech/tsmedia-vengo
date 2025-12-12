#!/usr/bin/env python3
"""
Example of how to use the WiFi Manager module
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from wifi_manager import WiFiManager

def main():
    # Create WiFi manager instance with custom settings
    manager = WiFiManager(
        ssid="MyDevice-Setup",          # Hotspot name
        password="mypassword123",       # Hotspot password  
        interface="wlan0"               # WiFi interface
    )
    
    print("Starting WiFi Manager...")
    print("This will:")
    print("1. Create a WiFi hotspot")
    print("2. Display a QR code to connect")
    print("3. Provide a web interface for WiFi setup")
    print("4. Save WiFi credentials and reconnect")
    
    try:
        manager.run()
    except KeyboardInterrupt:
        print("\nStopped by user")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Please run with sudo:")
        print("sudo python3 example.py")
        sys.exit(1)
    
    main()
