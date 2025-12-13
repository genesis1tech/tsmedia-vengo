#!/usr/bin/env python3

"""
Simple Barcode Reader for Raspberry Pi
Optimized for continuous scanning of numeric barcodes
Supports both USB-HID-KBW scanner input devices and serial scanners
"""

import sys
import time
import signal
import argparse
from datetime import datetime
import struct
import select
import os
import grp
import pwd

# Try to import serial for serial scanner support
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

class BarcodeReader:
    def __init__(self, quiet=False, serial_port=None, baud_rate=9600):
        self.scan_count = 0
        self.running = True
        self.current_barcode = ""
        self.quiet = quiet

        # Scanner mode: 'hid', 'serial', or None (auto-detect)
        self.scanner_mode = None

        # Serial scanner settings
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self._serial_handle = None

        # Persistent device handle management (for HID mode)
        self._device_handle = None
        self._device_path = None
        self._last_access_time = 0
        self._handle_timeout = 30  # Close handle after 30 seconds of inactivity

        # Updated keycode mapping - focusing on numbers and common barcode characters
        self.keycode_map = {
            # Number keys (main keyboard)
            2: '1', 3: '2', 4: '3', 5: '4', 6: '5',
            7: '6', 8: '7', 9: '8', 10: '9', 11: '0',

            # Keypad numbers (alternative) - more common on USB barcode scanners
            79: '1', 80: '2', 81: '3', 75: '4', 76: '5',
            77: '6', 71: '7', 72: '8', 73: '9', 82: '0',

            # Common barcode special characters
            12: '-',    # Minus/dash (common in some barcodes)
            52: '.',    # Period/dot
            53: '/',    # Forward slash

            # Space key (for some barcode formats)
            57: ' ',    # Spacebar
        }

        # Auto-detect scanner mode on initialization
        self._detect_scanner_mode()

        # Setup signal handler for clean exit
        signal.signal(signal.SIGINT, self.signal_handler)

    def _detect_scanner_mode(self):
        """Auto-detect the scanner type (serial or HID)"""
        # First, check for serial scanner (preferred for reliability)
        serial_port = self._find_serial_scanner()
        if serial_port:
            self.scanner_mode = 'serial'
            self.serial_port = serial_port
            if not self.quiet:
                self.log_message(f"Detected serial scanner at {serial_port}")
            return

        # Fall back to HID keyboard scanner
        hid_device = self.find_scanner_device()
        if hid_device:
            self.scanner_mode = 'hid'
            if not self.quiet:
                self.log_message(f"Detected HID scanner at {hid_device}")
            return

        if not self.quiet:
            self.log_message("No scanner detected", "WARNING")

    def _find_serial_scanner(self):
        """Find serial scanner port (not servo controllers)"""
        if not SERIAL_AVAILABLE:
            return None

        # If a specific serial port was provided, use it
        if self.serial_port and os.path.exists(self.serial_port):
            return self.serial_port

        # Check using pyserial's port detection
        # Look for devices that are explicitly barcode scanners, not servo controllers
        try:
            ports = serial.tools.list_ports.comports()
            for port in ports:
                desc = str(port.description).lower() if port.description else ''
                # Skip known servo controller adapters (115200 baud, Waveshare Bus Servo Adapter)
                # The QinHeng 1a86:55d3 with product "USB Single Serial" is typically a servo adapter
                if port.vid == 0x1a86 and port.pid == 0x55d3:
                    # This is likely the Waveshare Bus Servo Adapter - skip it
                    continue
                # Look for devices that are explicitly scanners
                if 'scanner' in desc or 'barcode' in desc:
                    return port.device
        except Exception:
            pass

        return None

    def _open_serial(self):
        """Open serial connection to scanner"""
        if not SERIAL_AVAILABLE or not self.serial_port:
            return False

        try:
            if self._serial_handle and self._serial_handle.is_open:
                return True

            self._serial_handle = serial.Serial(
                port=self.serial_port,
                baudrate=self.baud_rate,
                timeout=0.01,  # 10ms timeout for responsive scanning
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )

            if not self.quiet:
                self.log_message(f"Opened serial port: {self.serial_port}")
            return True

        except Exception as e:
            self.log_message(f"Failed to open serial port: {e}", "ERROR")
            return False

    def _close_serial(self):
        """Close serial connection"""
        if self._serial_handle and self._serial_handle.is_open:
            try:
                self._serial_handle.close()
            except Exception:
                pass
            self._serial_handle = None

    def _read_serial_barcode(self, timeout_sec=None):
        """Read barcode from serial scanner"""
        if not self._open_serial():
            return None

        start_time = time.time()
        buffer = ""

        try:
            while self.running:
                # Check timeout
                if timeout_sec and (time.time() - start_time) > timeout_sec:
                    break

                # Check for available data
                if self._serial_handle.in_waiting > 0:
                    data = self._serial_handle.read(self._serial_handle.in_waiting)
                    try:
                        text = data.decode('utf-8')
                    except UnicodeDecodeError:
                        text = data.decode('latin-1', errors='ignore')

                    buffer += text

                    # Check for line ending (barcode complete)
                    if '\r' in buffer or '\n' in buffer:
                        # Extract the barcode
                        barcode = buffer.strip('\r\n').strip()
                        if barcode:
                            return barcode
                        buffer = ""

                # Small sleep to prevent CPU spinning
                time.sleep(0.001)

        except Exception as e:
            self.log_message(f"Serial read error: {e}", "ERROR")

        return None
    
    def signal_handler(self, sig, frame):
        """Handle Ctrl+C gracefully"""
        if not self.quiet:
            print(f"\nScan session ended. Total scans: {self.scan_count}", file=sys.stderr)
        self.running = False
        self._close_device_handle()  # Clean up persistent HID handle
        self._close_serial()  # Clean up serial handle
        sys.exit(0)
    
    def log_message(self, message, level="INFO"):
        """Log message only if not in quiet mode"""
        if not self.quiet:
            if level == "ERROR":
                print(f"ERROR: {message}", file=sys.stderr)
            elif level == "WARNING":
                print(f"WARNING: {message}", file=sys.stderr)
            else:
                print(f"INFO: {message}", file=sys.stderr)
    
    def is_qr_code(self, barcode_data):
        """
        Detect if scanned data is a QR code
        
        QR codes typically contain:
        - Text characters (letters)
        - HTTP/HTTPS URLs
        - Forward slashes (/)
        
        Barcodes in this system are always numeric
        
        Args:
            barcode_data: The scanned data string
            
        Returns:
            bool: True if likely a QR code, False if likely a barcode
        """
        if not barcode_data or not barcode_data.strip():
            return False
            
        barcode_data = barcode_data.strip()
        
        # If contains any letters (a-z, A-Z), it's likely a QR code
        if any(c.isalpha() for c in barcode_data):
            return True
            
        # If contains common URL patterns, it's definitely a QR code
        if 'http' in barcode_data.lower() or 'www.' in barcode_data.lower():
            return True
            
        # If contains forward slash, it's likely a QR code (URLs, paths)
        if '/' in barcode_data:
            return True
            
        # If contains other special characters commonly in QR codes
        special_chars = ['?', '=', '&', ':', '@', '#', '%', '+', '_']
        if any(char in barcode_data for char in special_chars):
            return True
            
        # Check if it's numeric (allowing dashes which are common in barcodes)
        # Remove dashes and check if remaining is all digits
        numeric_without_dashes = barcode_data.replace('-', '')
        if numeric_without_dashes.isdigit() and not any(c.isalpha() for c in barcode_data):
            return False
            
        # Default to treating as QR code if unsure
        return True
    
    def check_input_access(self):
        """Check if user has access to input devices"""
        try:
            username = pwd.getpwuid(os.getuid()).pw_name
            user_groups = [g.gr_name for g in grp.getgrall() if username in g.gr_mem]
            primary_group = grp.getgrgid(os.getgid()).gr_name
            all_groups = user_groups + [primary_group]
            
            has_input_access = 'input' in all_groups
            is_root = os.geteuid() == 0
            
            return has_input_access or is_root, all_groups
        except Exception as e:
            self.log_message(f"Could not check group membership: {e}", "WARNING")
            return False, []
    
    def setup_input_access(self):
        """Setup access to input devices"""
        has_access, user_groups = self.check_input_access()
        
        if not has_access:
            self.log_message("No input device access detected", "WARNING")
            if not self.quiet:
                username = pwd.getpwuid(os.getuid()).pw_name
                print(f"Current user groups: {', '.join(user_groups)}", file=sys.stderr)
                print("Solutions:", file=sys.stderr)
                print(f"  1. Add user to input group: sudo usermod -a -G input {username}", file=sys.stderr)
                print("  2. Log out and back in (or reboot)", file=sys.stderr)
                print(f"  3. Or run with: sudo python3 {sys.argv[0]}", file=sys.stderr)
            return False
        
        return True
    
    def find_scanner_device(self):
        """Find the scanner input device"""
        # First: search for keyboard devices by name
        for i in range(10):
            device = f"/dev/input/event{i}"
            if os.path.exists(device):
                try:
                    with open(f"/sys/class/input/event{i}/device/name", 'r') as f:
                        name = f.read().strip()
                        if "keyboard" in name.lower() or "26f1:5651" in name or "dwc2-gadget" in name:
                            with open(device, 'rb') as f:
                                f.read(0)  # Test read access
                            return device
                except (FileNotFoundError, PermissionError):
                    continue
                except Exception:
                    continue
        
        return None
        

    def read_input_events_with_handle(self, device_handle, device_path, timeout_sec=None):
        """Read raw input events from persistent device handle"""
        try:
            start_time = time.time()
            
            while self.running:
                # Check timeout
                if timeout_sec and (time.time() - start_time) > timeout_sec:
                    break
                
                # Check if data is available
                ready, _, _ = select.select([device_handle], [], [], 0.001)  # 1ms for rapid multi-scan support
                
                if ready:
                    data = device_handle.read(24)
                    if len(data) == 24:
                        tv_sec, tv_usec, event_type, event_code, event_value = struct.unpack('llHHI', data)
                        
                        # Key events (type 1) with press (value 1)
                        if event_type == 1 and event_value == 1:
                            if event_code == 28:  # Enter key - end of barcode
                                if self.current_barcode:
                                    yield self.current_barcode
                                    self.current_barcode = ""
                                    start_time = time.time()  # Reset timeout
                            elif event_code in self.keycode_map:
                                self.current_barcode += self.keycode_map[event_code]
                    elif len(data) == 0:
                        # Handle closed - device may have been disconnected
                        self.log_message("Device handle closed unexpectedly", "WARNING")
                        self._refresh_device_handle()
                        break
                        
        except Exception as e:
            self.log_message(f"Error reading from device handle: {e}", "ERROR")
            # Try to refresh handle on error
            self._refresh_device_handle()

    def read_input_events(self, device_path, timeout_sec=None):
        """Read raw input events from device"""
        try:
            with open(device_path, 'rb') as device:
                self.log_message(f"Reading from device: {device_path}")
                
                start_time = time.time()
                
                while self.running:
                    # Check timeout
                    if timeout_sec and (time.time() - start_time) > timeout_sec:
                        break
                    
                    # Check if data is available
                    ready, _, _ = select.select([device], [], [], 0.001)  # 1ms for rapid multi-scan support
                    
                    if ready:
                        data = device.read(24)
                        if len(data) == 24:
                            tv_sec, tv_usec, event_type, event_code, event_value = struct.unpack('llHHI', data)
                            
                            # Key events (type 1) with press (value 1)
                            if event_type == 1 and event_value == 1:
                                if event_code == 28:  # Enter key - end of barcode
                                    if self.current_barcode:
                                        yield self.current_barcode
                                        self.current_barcode = ""
                                        start_time = time.time()  # Reset timeout
                                elif event_code in self.keycode_map:
                                    self.current_barcode += self.keycode_map[event_code]
                    
        except PermissionError:
            self.log_message(f"Permission denied accessing {device_path}", "ERROR")
            if not self.quiet:
                print("Solutions:", file=sys.stderr)
                print("  1. Add user to input group: sudo usermod -a -G input $USER", file=sys.stderr)
                print("  2. Log out and back in (or reboot)", file=sys.stderr)
                print("  3. Run setup script (recommended): ./setup_autostart.sh", file=sys.stderr)
                print("     This installs udev rules and adds user to input group", file=sys.stderr)
                print("  4. Or run with: sudo python3 barcode_reader.py", file=sys.stderr)
            return
        except FileNotFoundError:
            self.log_message(f"Device not found: {device_path}", "ERROR")
            return
        except Exception as e:
            self.log_message(f"Error reading from device: {e}", "ERROR")
            return
    

    def _open_device_handle(self, device_path):
        """Open and cache device handle"""
        try:
            # Close existing handle if it's for a different device
            if self._device_path != device_path:
                self._close_device_handle()
            
            # Open new handle if needed
            if self._device_handle is None:
                self._device_handle = open(device_path, 'rb')
                self._device_path = device_path
                if not self.quiet:
                    self.log_message(f"Opened persistent device handle: {device_path}")
            
            self._last_access_time = time.time()
            return self._device_handle
            
        except Exception as e:
            self.log_message(f"Failed to open device handle: {e}", "ERROR")
            self._close_device_handle()
            return None
    
    def _close_device_handle(self):
        """Close and clear device handle"""
        if self._device_handle:
            try:
                self._device_handle.close()
                if not self.quiet:
                    self.log_message(f"Closed device handle: {self._device_path}")
            except:
                pass
            finally:
                self._device_handle = None
                self._device_path = None
    
    def _check_handle_timeout(self):
        """Close handle if it's been inactive too long"""
        if (self._device_handle and 
            time.time() - self._last_access_time > self._handle_timeout):
            if not self.quiet:
                self.log_message("Closing inactive device handle")
            self._close_device_handle()
    
    def _refresh_device_handle(self):
        """Refresh device handle to clear any stale state"""
        if self._device_handle and self._device_path:
            self._close_device_handle()
            # Brief pause to let device settle
            time.sleep(0.005)  # 5ms pause
            return self._open_device_handle(self._device_path)
        return None

    def flush_input_buffer_async(self, device_path):
        """Flush any stale data from the input device buffer without blocking"""
        try:
            device = self._open_device_handle(device_path)
            if not device:
                return
                
            # Use the persistent handle for flushing
            # Quick non-blocking flush - read any immediately available data
            flushed_bytes = 0
            max_flushes = 10  # Limit to prevent infinite loops
            
            for _ in range(max_flushes):
                ready, _, _ = select.select([device], [], [], 0)  # No timeout - immediate check
                if ready:
                    data = device.read(24)
                    if len(data) > 0:
                        flushed_bytes += len(data)
                    else:
                        break  # No more data available
                else:
                    break  # No data ready
            
            if flushed_bytes > 0 and not self.quiet:
                self.log_message(f"Cleared {flushed_bytes} bytes from input buffer")
                
        except Exception as e:
            # Silently handle flush errors to avoid disrupting scanning
            pass
    
    def scan_single(self, timeout_sec=None):
        """Scan a single barcode and return it"""
        # Clear any existing barcode buffer to prevent concatenation
        self.current_barcode = ""

        # Use serial mode if detected
        if self.scanner_mode == 'serial':
            return self._read_serial_barcode(timeout_sec)

        # Fall back to HID keyboard mode
        device = self.find_scanner_device()
        if not device:
            self.log_message("Scanner device not found or not accessible", "ERROR")
            return None

        try:
            for barcode in self.read_input_events(device, timeout_sec):
                if barcode:
                    # Clear any remaining data in buffer after successful scan
                    # This prevents contamination affecting the next scan
                    self.flush_input_buffer_async(device)
                    return barcode
        except KeyboardInterrupt:
            return None
        except Exception as e:
            self.log_message(f"Error during scanning: {e}", "ERROR")
            return None

        return None
    
    def scan_continuous(self, timeout_sec=None, logfile=None):
        """Continuous barcode scanning"""
        self.log_message(f"Starting continuous barcode scanning (mode: {self.scanner_mode})")
        self.log_message("Press Ctrl+C to exit")

        # Use serial mode if detected
        if self.scanner_mode == 'serial':
            self._scan_continuous_serial(timeout_sec, logfile)
            return

        # Fall back to HID keyboard mode
        device = self.find_scanner_device()
        if not device:
            self.log_message("Scanner device not found or not accessible", "ERROR")
            return

        try:
            for barcode in self.read_input_events(device, timeout_sec):
                if barcode:
                    self._process_continuous_scan(barcode, logfile)

        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.log_message(f"Error during scanning: {e}", "ERROR")

    def _scan_continuous_serial(self, timeout_sec=None, logfile=None):
        """Continuous scanning in serial mode"""
        start_time = time.time()

        try:
            while self.running:
                # Check timeout
                if timeout_sec and (time.time() - start_time) > timeout_sec:
                    break

                barcode = self._read_serial_barcode(timeout_sec=1)  # 1 second timeout per read
                if barcode:
                    self._process_continuous_scan(barcode, logfile)
                    start_time = time.time()  # Reset timeout after successful scan

        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.log_message(f"Error during serial scanning: {e}", "ERROR")

    def _process_continuous_scan(self, barcode, logfile=None):
        """Process a barcode in continuous scanning mode"""
        self.scan_count += 1

        # Output only the barcode
        print(barcode)
        sys.stdout.flush()  # Ensure immediate output

        # Log to file if specified
        if logfile:
            try:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with open(logfile, 'a') as f:
                    f.write(f"{timestamp} - {barcode}\n")
            except Exception as e:
                self.log_message(f"Failed to log to file: {e}", "ERROR")
    
    def display_header(self):
        """Display startup header (only if not quiet)"""
        if not self.quiet:
            print("Barcode Scanner v2.4 - Ready", file=sys.stderr)
            print(f"Scanner mode: {self.scanner_mode or 'not detected'}", file=sys.stderr)
            if self.scanner_mode == 'serial':
                print(f"Serial port: {self.serial_port}", file=sys.stderr)
            has_access, _ = self.check_input_access()
            if has_access:
                print("Input access: OK", file=sys.stderr)
            else:
                print("Input access: Limited", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="Barcode Reader - Returns only barcode data")
    parser.add_argument('-s', '--single', action='store_true',
                       help='Scan single barcode and exit')
    parser.add_argument('-l', '--log', metavar='FILE',
                       help='Log scanned barcodes to file')
    parser.add_argument('-t', '--timeout', type=int, metavar='SEC',
                       help='Set timeout in seconds')
    parser.add_argument('-q', '--quiet', action='store_true',
                       help='Quiet mode - output only barcodes')
    parser.add_argument('--force', action='store_true',
                       help='Force run without access checks')
    parser.add_argument('--version', action='version', version='Barcode Reader 2.3')
    
    args = parser.parse_args()
    
    # Create reader instance
    reader = BarcodeReader(quiet=args.quiet)
    
    # Display header unless quiet
    if not args.quiet:
        reader.display_header()
    
    # Check input access unless forced
    if not args.force:
        if not reader.setup_input_access():
            sys.exit(1)
    
    try:
        if args.single:
            # Single scan mode
            barcode = reader.scan_single(args.timeout)
            if barcode:
                print(barcode)
            else:
                sys.exit(1)
        else:
            # Continuous scan mode (default)
            reader.scan_continuous(args.timeout, args.log)
            
    except KeyboardInterrupt:
        reader.signal_handler(signal.SIGINT, None)
    except Exception as e:
        reader.log_message(f"Unexpected error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
