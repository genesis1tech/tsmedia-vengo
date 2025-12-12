#!/usr/bin/env python3
"""
Test WiFi Fixes - Comprehensive WiFi Information Testing
Tests both AWSManager and ResilientAWSManager WiFi extraction
"""

import sys
import os
import json
import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def test_commands_exist():
    """Test that WiFi commands exist"""
    print("=" * 70)
    print("1. Testing WiFi Command Availability")
    print("=" * 70)
    
    commands = [
        ('/usr/sbin/iwgetid', 'Full path iwgetid'),
        ('/usr/sbin/iwconfig', 'Full path iwconfig'),
        ('iwgetid', 'PATH iwgetid'),
        ('iwconfig', 'PATH iwconfig')
    ]
    
    for cmd, desc in commands:
        if '/' in cmd:
            exists = os.path.exists(cmd)
            print(f"  {desc}: {'✅ Found' if exists else '❌ Not found'} at {cmd}")
        else:
            import shutil
            exists = shutil.which(cmd) is not None
            print(f"  {desc}: {'✅ Found' if exists else '❌ Not found'} in PATH")
    print()

def test_direct_commands():
    """Test running WiFi commands directly"""
    print("=" * 70)
    print("2. Testing Direct Command Execution")
    print("=" * 70)
    
    import subprocess
    
    # Test iwgetid
    print("  Testing iwgetid:")
    for cmd in ['/usr/sbin/iwgetid', 'iwgetid']:
        try:
            result = subprocess.run([cmd, '-r'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print(f"    ✅ {cmd}: {result.stdout.strip()}")
                break
            else:
                print(f"    ⚠️  {cmd} failed (code {result.returncode})")
        except FileNotFoundError:
            print(f"    ❌ {cmd} not found")
        except Exception as e:
            print(f"    ❌ {cmd} error: {e}")
    
    # Test iwconfig
    print("  Testing iwconfig:")
    for cmd in ['/usr/sbin/iwconfig', 'iwconfig']:
        try:
            result = subprocess.run([cmd], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'Signal level=' in line:
                        print(f"    ✅ {cmd}: Found signal line")
                        print(f"       {line.strip()}")
                        break
                break
            else:
                print(f"    ⚠️  {cmd} failed (code {result.returncode})")
        except FileNotFoundError:
            print(f"    ❌ {cmd} not found")
        except Exception as e:
            print(f"    ❌ {cmd} error: {e}")
    print()

def test_resilient_manager():
    """Test ResilientAWSManager WiFi extraction"""
    print("=" * 70)
    print("3. Testing ResilientAWSManager._get_wifi_info()")
    print("=" * 70)
    
    try:
        from tsv6.core.aws_resilient_manager import ResilientAWSManager
        
        manager = ResilientAWSManager(
            thing_name='TEST',
            endpoint='test.iot.us-east-1.amazonaws.com',
            cert_path='/tmp/fake',
            key_path='/tmp/fake',
            ca_path='/tmp/fake'
        )
        
        print("  Calling _get_wifi_info()...")
        wifi_ssid, wifi_strength = manager._get_wifi_info()
        
        print(f"\n  Results:")
        print(f"    SSID: {wifi_ssid}")
        print(f"    Signal Strength: {wifi_strength} dBm")
        
        if wifi_ssid != "Unknown":
            print(f"    ✅ SSID detected correctly")
        else:
            print(f"    ❌ SSID is 'Unknown' - check error messages above")
        
        if wifi_strength != -50:
            print(f"    ✅ Signal strength detected correctly")
        else:
            print(f"    ⚠️  Signal strength is default value")
        
        # Show what would be sent to AWS
        print(f"\n  AWS IoT Shadow Payload:")
        status = {
            "thingName": "TEST_DEVICE",
            "wifiSSID": wifi_ssid,
            "wifiStrength": wifi_strength,
            "temperature": 72.0,
            "timestampISO": datetime.datetime.utcnow().isoformat() + "Z"
        }
        shadow_payload = {"state": {"reported": status}}
        print("  " + json.dumps(shadow_payload, indent=4).replace('\n', '\n  '))
        
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    print()

def test_aws_manager():
    """Test AWSManager WiFi extraction"""
    print("=" * 70)
    print("4. Testing AWSManager._get_wifi_info()")
    print("=" * 70)
    
    try:
        from tsv6.core.aws_manager import AWSManager
        
        manager = AWSManager(
            thing_name='TEST',
            endpoint='test.iot.us-east-1.amazonaws.com',
            cert_path='/tmp/fake',
            key_path='/tmp/fake',
            ca_path='/tmp/fake'
        )
        
        print("  Calling _get_wifi_info()...")
        wifi_ssid, wifi_strength = manager._get_wifi_info()
        
        print(f"\n  Results:")
        print(f"    SSID: {wifi_ssid}")
        print(f"    Signal Strength: {wifi_strength} dBm")
        
        if wifi_ssid != "Unknown":
            print(f"    ✅ SSID detected correctly")
        else:
            print(f"    ❌ SSID is 'Unknown' - check error messages above")
        
        if wifi_strength != -50:
            print(f"    ✅ Signal strength detected correctly")
        else:
            print(f"    ⚠️  Signal strength is default value")
        
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    print()

def main():
    """Run all tests"""
    print("\n")
    print("🔍 WiFi Information Extraction Test Suite")
    print("=" * 70)
    print()
    
    test_commands_exist()
    test_direct_commands()
    test_resilient_manager()
    test_aws_manager()
    
    print("=" * 70)
    print("✅ Test Suite Complete")
    print("=" * 70)
    print("\nIf all tests pass, the WiFi data should now be correctly")
    print("sent to AWS IoT when the production service runs.")
    print()

if __name__ == "__main__":
    main()
