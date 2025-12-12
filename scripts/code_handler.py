import sys
import signal
import threading
import time

class KeyboardBarcodeScanner:
    def __init__(self):
        """Initialize the scanner with graceful exit handling."""
        self.running = True
        self.scan_callback = None
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        print(f"\nReceived signal {signum}, shutting down gracefully...")
        self.running = False
        sys.exit(0)  # Ensure clean exit to command prompt
    
    def set_scan_callback(self, callback):
        """Set callback function to be called when barcode is scanned."""
        self.scan_callback = callback
    
    def read_barcode(self):
        """Read a barcode from keyboard (HID-KBW) input."""
        if not self.running:
            return None
        
        try:
            # Read input from barcode scanner (appears as keyboard input)
            line = input().strip()
            return line if line else None
        except (EOFError, KeyboardInterrupt):
            print("\nInput interrupted, stopping scanner...")
            self.running = False
            return None
        except Exception as e:
            print(f"Error reading barcode: {e}")
            return None
    
    def start_scanning(self):
        """Start the barcode scanning loop with graceful exit."""
        print("Barcode Scanner Started")
        print("Scan a barcode (Ctrl+C to exit):")
        
        try:
            while self.running:
                barcode = self.read_barcode()
                
                if not self.running:
                    break
                
                if barcode:
                    print(f"Scanned: {barcode}")
                    
                    # Call callback if set
                    if self.scan_callback:
                        try:
                            self.scan_callback(barcode)
                        except Exception as e:
                            print(f"Error in scan callback: {e}")
                
        except KeyboardInterrupt:
            print("\nKeyboard interrupt received...")
            self.running = False
        except Exception as e:
            print(f"Unexpected error: {e}")
        finally:
            self._cleanup()
    
    def scan_once(self, timeout=None):
        """Scan for a single barcode with optional timeout."""
        return self.read_barcode()
    
    def stop(self):
        """Stop the scanner gracefully."""
        print("Stopping barcode scanner...")
        self.running = False
    
    def _cleanup(self):
        """Perform cleanup operations."""
        print("Cleaning up barcode scanner resources...")
        self.running = False
        print("Barcode scanner stopped.")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self._cleanup()
        if exc_type is KeyboardInterrupt:
            print("Exited due to keyboard interrupt")
            return True  # Suppress the exception
        return False

# Example usage
if __name__ == "__main__":
    try:
        with KeyboardBarcodeScanner() as scanner:
            scanner.start_scanning()
    except KeyboardInterrupt:
        print("\nApplication interrupted by user")
    except Exception as e:
        print(f"Application error: {e}")
    finally:
        print("Application terminated.")
        sys.exit(0)  # Ensure clean exit to command prompt