#!/usr/bin/env python3
"""
UART Connection and Signal Test
Tests UART functionality and signal integrity
"""

import serial
import time
import os

def test_uart_loopback():
    """Test UART by connecting TX to RX (loopback test)"""
    print("🔄 UART Loopback Test")
    print("For this test, temporarily connect Pin 8 (TX) to Pin 10 (RX)")
    print("This will test if the UART is working properly")
    
    try:
        response = input("Connect TX to RX and press Enter (or 's' to skip): ")
        if response.lower() == 's':
            return
            
        ser = serial.Serial('/dev/ttyS0', 9600, timeout=1)
        
        # Send test data
        test_message = b"TEST123\r\n"
        print(f"Sending: {test_message}")
        
        ser.write(test_message)
        time.sleep(0.1)
        
        # Try to read back
        if ser.in_waiting > 0:
            received = ser.read_all()
            print(f"✅ Received: {received}")
            print("UART is working correctly!")
        else:
            print("❌ No data received - UART may not be configured properly")
            
        ser.close()
        
    except Exception as e:
        print(f"❌ Loopback test failed: {e}")

def check_uart_status():
    """Check UART configuration and status"""
    print("\n📊 UART Status Check")
    print("=" * 30)
    
    # Check if UART is enabled
    try:
        with open('/boot/firmware/config.txt', 'r') as f:
            config_content = f.read()
            if 'enable_uart=1' in config_content:
                print("✅ UART enabled in config.txt")
            else:
                print("❌ UART not enabled - add 'enable_uart=1' to /boot/firmware/config.txt")
    except:
        print("❌ Could not check config.txt")
    
    # Check serial devices
    if os.path.exists('/dev/ttyS0'):
        print("✅ /dev/ttyS0 exists")
    else:
        print("❌ /dev/ttyS0 not found")
        
    if os.path.exists('/dev/serial0'):
        print("✅ /dev/serial0 exists")
    else:
        print("❌ /dev/serial0 not found")

def connection_guide():
    """Display connection guide"""
    print("\n📋 Connection Guide")
    print("=" * 20)
    print("Scanner → Raspberry Pi")
    print("Scanner TX → Pi Pin 10 (GPIO15/RX)")
    print("Scanner RX → Pi Pin 8  (GPIO14/TX)")  
    print("Scanner VCC → Pi Pin 2 (5V)")
    print("Scanner GND → Pi Pin 6 (GND)")
    print("\n💡 Important Notes:")
    print("- TX connects to RX (transmit to receive)")
    print("- RX connects to TX (receive to transmit)")
    print("- Double-check voltage levels (3.3V vs 5V)")
    print("- Some scanners may need pull-up resistors")

def main():
    print("UART Connection Diagnostic Tool")
    print("=" * 35)
    
    check_uart_status()
    connection_guide()
    test_uart_loopback()
    
    print(f"\n🔍 Next Steps:")
    print(f"1. Verify all connections match the guide above")
    print(f"2. Check scanner is powered and configured for 9600 baud serial")
    print(f"3. Try the loopback test to verify UART is working")
    print(f"4. Run the monitor again: python simple_uart_monitor.py")

if __name__ == "__main__":
    main()
