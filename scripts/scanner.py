#!/usr/bin/env python3
"""
Universal Barcode Scanner Module
Automatically detects and handles both serial and HID keyboard scanners
"""

import os
import sys
import threading
import time
import datetime
from collections import deque
import select
import termios
import tty

# Try to import serial for serial scanner support
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("pyserial not available - only HID keyboard mode supported")

class BarcodeScanner:
    def __init__(self, callback=None, auto_detect=True, serial_port='/dev/ttyS0', baud_rate=9600):
        """
        Initialize the barcode scanner with auto-detection
        
        Args:
            callback: Function to call when a barcode is scanned
            auto_detect: Automatically detect scanner type
            serial_port: Serial port for serial scanners
            baud_rate: Baud rate for serial communication
        """
        self.callback = callback
        self.auto_detect = auto_detect
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.scanner_mode = None  # 'serial' or 'hid'
        self.ser = None
        self.scan_history = deque(maxlen=100)
        self.total_scans = 0
        self.running = False
        self.scanner_thread = None
        self.input_buffer = ""
        
    def start_scanner(self):
        """Start the scanner with auto-detection"""
        if self.running:
            print("Scanner is already running")
            return False
        
        # Auto-detect scanner type
        if self.auto_detect:
            self.scanner_mode = self._detect_scanner_type()
        
        if self.scanner_mode == 'serial':
            return self._start_serial_scanner()
        elif self.scanner_mode == 'hid':
            return self._start_hid_scanner()
        else:
            print("✗ No compatible scanner detected")
            return False
    
    def _detect_scanner_type(self):
        """Auto-detect the type of scanner connected"""
        print("🔍 Auto-detecting scanner type...")
        
        # Check for HID keyboard scanner
        hid_scanners = self._check_hid_scanners()
        if hid_scanners:
            print(f"✓ Found HID keyboard scanner: {hid_scanners[0]}")
            return 'hid'
        
        # Check for serial scanner
        if SERIAL_AVAILABLE and self._check_serial_scanner():
            print("✓ Found serial scanner")
            return 'serial'
            
        print("✗ No scanner detected")
        return None
    
    def _check_hid_scanners(self):
        """Check for HID keyboard scanners"""
        try:
            # Look for common barcode scanner USB IDs
            with open('/proc/bus/input/devices', 'r') as f:
                content = f.read()
                
            # Look for keyboard devices that might be scanners
            scanner_keywords = ['keyboard', 'barcode', 'scanner', 'dwc2-gadget']
            
            devices = []
            for section in content.split('\n\n'):
                if any(keyword.lower() in section.lower() for keyword in scanner_keywords):
                    # Extract device name
                    for line in section.split('\n'):
                        if line.startswith('N: Name='):
                            name = line.split('=', 1)[1].strip('"')
                            if 'keyboard' in name.lower() or 'dwc2-gadget' in name.lower():
                                devices.append(name)
                                
            return devices
        except Exception as e:
            print(f"Error checking HID devices: {e}")
            return []
    
    def _check_serial_scanner(self):
        """Check for serial scanner"""
        if not SERIAL_AVAILABLE:
            return False
            
        try:
            ser = serial.Serial(
                port=self.serial_port,
                baudrate=self.baud_rate,
                timeout=0.1
            )
            ser.close()
            return True
        except:
            return False
    
    def _start_serial_scanner(self):
        """Start serial scanner mode"""
        if not SERIAL_AVAILABLE:
            print("✗ Serial support not available")
            return False
            
        if not self._setup_serial():
            return False
            
        self.running = True
        self.scanner_thread = threading.Thread(target=self._serial_scanner_loop, daemon=True)
        self.scanner_thread.start()
        print("✓ Serial scanner started")
        return True
    
    def _start_hid_scanner(self):
        """Start HID keyboard scanner mode"""
        self.running = True
        self.scanner_thread = threading.Thread(target=self._hid_scanner_loop, daemon=True)
        self.scanner_thread.start()
        print("✓ HID keyboard scanner started")
        return True
    
    def _setup_serial(self):
        """Setup serial connection"""
        try:
            self.ser = serial.Serial(
                port=self.serial_port,
                baudrate=self.baud_rate,
                timeout=1
            )
            return True
        except Exception as e:
            print(f"✗ Serial setup failed: {e}")
            return False
    
    def _serial_scanner_loop(self):
        """Serial scanner main loop"""
        print("Serial scanner ready - scan a barcode...")
        
        while self.running:
            try:
                if self.ser and self.ser.is_open and self.ser.in_waiting > 0:
                    raw_data = self.ser.readline()
                    try:
                        barcode = raw_data.decode('utf-8').strip()
                        if barcode:
                            self._process_barcode(barcode)
                    except UnicodeDecodeError:
                        try:
                            barcode = raw_data.decode('latin-1').strip()
                            if barcode:
                                self._process_barcode(barcode)
                        except:
                            pass
                            
                time.sleep(0.1)
                
            except Exception as e:
                print(f"Serial scanner error: {e}")
                time.sleep(1)
                
        self.running = False
    
    def _hid_scanner_loop(self):
        """HID keyboard scanner main loop"""
        old_settings = None
        try:
            # Save and set terminal to raw mode for character capture
            old_settings = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
            
            print("HID scanner ready - scan a barcode or press Ctrl+C to exit")
            
            while self.running:
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    char = sys.stdin.read(1)
                    
                    if ord(char) == 3:  # Ctrl+C
                        break
                    elif ord(char) == 13 or ord(char) == 10:  # Enter/Return
                        if self.input_buffer.strip():
                            self._process_barcode(self.input_buffer.strip())
                            self.input_buffer = ""
                    elif ord(char) >= 32 and ord(char) <= 126:  # Printable characters
                        self.input_buffer += char
                        
        except Exception as e:
            print(f"HID scanner error: {e}")
        finally:
            # Restore terminal settings
            if old_settings:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
                except:
                    pass
                    
        self.running = False
    
    def stop_scanner(self):
        """Stop the scanner"""
        self.running = False
        if self.scanner_thread and self.scanner_thread.is_alive():
            self.scanner_thread.join(timeout=2)
        if self.ser and self.ser.is_open:
            self.ser.close()
        print("✓ Scanner stopped")
    
    def _process_barcode(self, barcode_data):
        """Process a complete barcode scan"""
        barcode = barcode_data.strip()
        
        if not barcode:
            return
            
        # Record the scan
        scan_time = datetime.datetime.now()
        self.total_scans += 1
        
        scan_record = {
            'barcode': barcode,
            'timestamp': scan_time,
            'scan_number': self.total_scans
        }
        self.scan_history.append(scan_record)
        
        # Print scan info
        print(f"\n{'='*50}")
        print(f"BARCODE SCANNED #{self.total_scans}")
        print(f"{'='*50}")
        print(f"Barcode: {barcode}")
        print(f"Length: {len(barcode)} characters")
        print(f"Time: {scan_time.strftime('%H:%M:%S')}")
        print(f"Type: {self._identify_barcode_type(barcode)}")
        print(f"Mode: {self.scanner_mode.upper()}")
        print(f"{'='*50}\n")
        
        # Call callback if provided
        if self.callback:
            try:
                self.callback(barcode, scan_record)
            except Exception as e:
                print(f"Error in callback: {e}")
    
    def _identify_barcode_type(self, barcode):
        """Simple barcode type identification"""
        length = len(barcode)
        
        if length == 12:
            return "UPC-A"
        elif length == 13:
            return "EAN-13"
        elif length == 8:
            return "EAN-8"
        elif length == 14:
            return "ITF-14/GTIN-14"
        elif barcode.startswith('01') and length > 14:
            return "GS1-128"
        else:
            return f"Unknown ({length} chars)"
    
    def get_scan_history(self, limit=10):
        """Get recent scan history"""
        return list(self.scan_history)[-limit:]
    
    def get_total_scans(self):
        """Get total number of scans"""
        return self.total_scans
    
    def is_running(self):
        """Check if scanner is running"""
        return self.running

def example_callback(barcode, scan_record):
    """Example callback function"""
    print(f"🔄 Processing barcode: {barcode}")
    
    # Example: Check if it's a recyclable item
    recyclable_prefixes = ['012', '070', '075', '041', '3']
    
    is_recyclable = any(barcode.startswith(prefix) for prefix in recyclable_prefixes)
    
    if is_recyclable:
        print("✅ Item is recyclable!")
    else:
        print("❓ Item recycling status unknown")

def main():
    """Main function for testing the scanner"""
    print("Universal Barcode Scanner")
    print("=" * 30)
    
    def scan_callback(barcode, scan_record):
        example_callback(barcode, scan_record)
        
    scanner = BarcodeScanner(callback=scan_callback)
    
    try:
        if not scanner.start_scanner():
            print("Failed to start scanner")
            return
            
        # Keep main thread alive
        while scanner.is_running():
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("\nShutting down scanner...")
    finally:
        scanner.stop_scanner()
        
        # Print final statistics
        print(f"\nScan Summary:")
        print(f"Total scans: {scanner.get_total_scans()}")
        
        if scanner.get_total_scans() > 0:
            print(f"\nRecent scans:")
            for i, scan in enumerate(scanner.get_scan_history(5), 1):
                print(f"  {i}. {scan['barcode']} at {scan['timestamp'].strftime('%H:%M:%S')}")

if __name__ == "__main__":
    main()
