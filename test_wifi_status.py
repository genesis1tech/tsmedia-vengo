#!/usr/bin/env python3
"""
Test WiFi Information Extraction and Status Publishing
Tests the exact code used by ResilientAWSManager
"""

import subprocess
import datetime
import time
import json

def _get_wifi_info():
    """Get WiFi information - exact copy from ResilientAWSManager"""
    try:
        # Get SSID
        result = subprocess.run(['iwgetid', '-r'], capture_output=True, text=True, timeout=5)
        ssid = result.stdout.strip() if result.returncode == 0 else "Unknown"
        
        # Get signal strength
        result = subprocess.run(['iwconfig'], capture_output=True, text=True, timeout=5)
        rssi = -50
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'Signal level=' in line:
                    try:
                        signal_part = line.split('Signal level=')[1].split()[0]
                        rssi = int(signal_part)
                    except:
                        pass
        
        return ssid, rssi
    except Exception as e:
        print(f"Error getting WiFi info: {e}")
        return "Unknown", -50

def _get_cpu_temperature():
    """Get CPU temperature in Fahrenheit"""
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp_celsius = int(f.read().strip()) / 1000.0
            temp_fahrenheit = (temp_celsius * 9/5) + 32
            return round(temp_fahrenheit, 1)
    except:
        return 75.0

def test_wifi_info():
    """Test WiFi information extraction"""
    print("=" * 70)
    print("Testing WiFi Information Extraction")
    print("=" * 70)
    
    # Get WiFi info
    wifi_ssid, wifi_strength = _get_wifi_info()
    cpu_temp = _get_cpu_temperature()
    
    print(f"\n✅ WiFi Information Retrieved:")
    print(f"   SSID: {wifi_ssid}")
    print(f"   Signal Strength: {wifi_strength} dBm")
    print(f"   CPU Temperature: {cpu_temp}°F")
    
    # Build the exact status structure that would be sent
    status = {
        "thingName": "TSV6_RPI_DEVICE",
        "deviceType": "raspberry-pi",
        "firmwareVersion": "6.0.0",
        "wifiSSID": wifi_ssid,
        "wifiStrength": wifi_strength,
        "temperature": cpu_temp,
        "timestampISO": datetime.datetime.utcnow().isoformat() + "Z",
        "timeConnectedMins": 5,
        "connectionState": "connected"
    }
    
    shadow_payload = {
        "state": {
            "reported": status
        }
    }
    
    print(f"\n📡 AWS IoT Shadow Payload that would be published:")
    print(json.dumps(shadow_payload, indent=2))
    
    # Verify the data is correct
    print(f"\n🔍 Verification:")
    if wifi_ssid != "Unknown":
        print(f"   ✅ WiFi SSID detected: {wifi_ssid}")
    else:
        print(f"   ❌ WiFi SSID not detected (showing 'Unknown')")
    
    if wifi_strength != -50:
        print(f"   ✅ WiFi strength detected: {wifi_strength} dBm")
    else:
        print(f"   ⚠️  WiFi strength using default: {wifi_strength} dBm")
    
    print(f"\n{'=' * 70}")
    print("Test Complete")
    print("=" * 70)

if __name__ == "__main__":
    test_wifi_info()
