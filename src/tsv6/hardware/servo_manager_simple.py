import time
import sys
import signal

class SimpleServoManager:
    """Simple servo manager using GPIO 18 directly"""
    
    def __init__(self, gpio_pin=18):
        """Initialize servo manager with GPIO 18"""
        self.gpio_pin = gpio_pin
        self.servo_open_position = 90
        self.servo_closed_position = 0
        self.door_open_duration = 3.0  # seconds
        self.servo_settle_time = 0.5  # seconds
        
        # Initialize simple servo controller
        self.servo = None
        self._init_servo()
        
        # Initialize servo to closed position
        if self.servo:
            self.set_angle(self.servo_closed_position)
            time.sleep(self.servo_settle_time)
            print("Door servo initialized to closed position")

    def _init_servo(self):
        """Initialize simple GPIO servo control"""
        try:
            from tsv6.hardware.servo_controller_simple import SimpleServoController
            
            # Initialize servo controller with GPIO 18
            self.servo = SimpleServoController(gpio_pin=self.gpio_pin)
            
            print(f"Simple GPIO servo initialized successfully on GPIO {self.gpio_pin}")
            
        except ImportError as e:
            print(f"Servo controller not found: {e}")
            self.servo = None
        except Exception as e:
            print(f"Failed to initialize servo on GPIO {self.gpio_pin}: {e}")
            self.servo = None

    def set_angle(self, angle):
        """Set servo to specified angle (0-180 degrees)"""
        if not self.servo:
            print("GPIO servo not initialized")
            return False
        
        try:
            # Clamp angle to valid range (0-180)
            angle = max(0, min(180, angle))
            
            # Use simple servo controller
            self.servo._set_angle(angle)
            
            time.sleep(self.servo_settle_time)
            print(f"GPIO servo set to {angle} degrees on GPIO {self.gpio_pin}")
            return True
            
        except Exception as e:
            print(f"Error setting GPIO servo angle: {e}")
            return False

    def door_open(self):
        """Open door, wait, then close it"""
        if not self.servo:
            print("Cannot open door - GPIO servo not initialized")
            return False
        
        try:
            print("Opening door...")
            self.set_angle(self.servo_open_position)
            
            print(f"Keeping door open for {self.door_open_duration} seconds...")
            time.sleep(self.door_open_duration)
            
            print("Closing door...")
            self.set_angle(self.servo_closed_position)
            
            print("Door operation completed")
            return True
        except Exception as e:
            print(f"Error during door operation: {e}")
            return False

    def door_close(self):
        """Explicitly close the door"""
        print("Closing door...")
        return self.set_angle(self.servo_closed_position)

    def door_open_only(self):
        """Open door without automatically closing"""
        print("Opening door (manual close required)...")
        return self.set_angle(self.servo_open_position)

    def get_current_position(self):
        """Get current servo position"""
        if not self.servo:
            return None
        
        return self.servo.get_position()

    def test_servo(self):
        """Test servo movement: 0° -> 90° -> 0°"""
        if not self.servo:
            print("Cannot test servo - GPIO servo not initialized")
            return False
        
        print("Testing GPIO servo movement...")
        
        try:
            # Test simple movement
            print("Moving to 90 degrees...")
            self.set_angle(90)
            time.sleep(1)
            
            print("Moving to 0 degrees...")
            self.set_angle(0)
            time.sleep(1)
            
            print("GPIO servo test completed")
            return True
            
        except Exception as e:
            print(f"Servo test failed: {e}")
            return False

    def handle_open_door_command(self, topic=None, message=None):
        """Handle open door message from AWS IoT or other sources"""
        print("\n" + "="*40)
        print("Received door open command")
        if topic:
            print(f"Topic: {topic}")
        if message:
            print(f"Message: {message}")
        print("="*40 + "\n")
        
        return self.door_open()

    def cleanup(self):
        """Clean up servo resources"""
        if self.servo:
            try:
                # Return to closed position before cleanup
                print("Returning GPIO servo to closed position...")
                self.set_angle(self.servo_closed_position)
                time.sleep(self.servo_settle_time)
                
                # Cleanup servo controller
                self.servo.cleanup()
                print("GPIO servo returned to closed position")
            except Exception as e:
                print(f"Error during GPIO servo cleanup: {e}")
            
        print("GPIO servo resources cleaned up")

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup"""
        self.cleanup()

# Signal handler for graceful shutdown
def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    print(f"\nReceived signal {sig}, shutting down GPIO servo manager...")
    sys.exit(0)

def main():
    """Main function for testing GPIO servo manager"""
    print("Starting Simple GPIO Servo Manager Test...")
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        with SimpleServoManager(gpio_pin=18) as servo_manager:
            if not servo_manager.servo:
                print("Failed to initialize GPIO servo. Exiting...")
                return
            
            print("GPIO Servo Manager initialized successfully")
            print("Configuration:")
            print(f"  GPIO Pin: {servo_manager.gpio_pin}")
            print(f"  Open Position: {servo_manager.servo_open_position}°")
            print(f"  Closed Position: {servo_manager.servo_closed_position}°")
            print(f"  Door Open Duration: {servo_manager.door_open_duration}s")
            
            # Test servo functionality
            print("\nTesting servo functionality...")
            servo_manager.test_servo()
            
            print("\nTesting door open/close cycle...")
            servo_manager.door_open()
            
            print("\nGPIO servo manager ready. Press Ctrl+C to exit.")
            
            # Keep running for manual testing
            while True:
                user_input = input("\nEnter command (open/close/test/status/quit): ").strip().lower()
                
                if user_input == 'open':
                    servo_manager.door_open()
                elif user_input == 'close':
                    servo_manager.door_close()
                elif user_input == 'test':
                    servo_manager.test_servo()
                elif user_input == 'status':
                    position = servo_manager.get_current_position()
                    print(f"Current servo position: {position}°")
                elif user_input in ['quit', 'exit', 'q']:
                    break
                else:
                    print("Available commands: open, close, test, status, quit")
                    
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        print("GPIO servo manager shutdown complete")

if __name__ == '__main__':
    main()
