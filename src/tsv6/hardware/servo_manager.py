import time
import sys
import signal
from config import config

class ServoManager:
    def __init__(self, servo_pin=None):
        """Initialize servo manager with DFRobot HAT configuration"""
        self.servo_pin = servo_pin or 0  # Use PWM 0 on DFRobot HAT
        self.servo_open_position = config.servo.POSITION_OPEN
        self.servo_closed_position = config.servo.POSITION_CLOSED
        self.door_open_duration = config.servo.DOOR_OPEN_DURATION / 1000  # Convert ms to seconds
        self.servo_settle_time = config.servo.SERVO_SETTLE_TIME / 1000  # Convert ms to seconds
        
        # Initialize DFRobot HAT
        self.board = None
        self.servo = None
        self._init_dfrobot_hat()
        
        # Initialize servo to closed position
        if self.servo:
            self.set_angle(self.servo_closed_position)
            time.sleep(self.servo_settle_time)
            print("Door servo initialized to closed position")

    def _init_dfrobot_hat(self):
        """Initialize DFRobot HAT I2C communication and servo control"""
        try:
            # Import DFRobot libraries
            from DFRobot_RaspberryPi_Expansion_Board import DFRobot_Expansion_Board_IIC as Board
            from DFRobot_RaspberryPi_Expansion_Board import DFRobot_Expansion_Board_Servo as Servo
            
            # Initialize board with I2C bus 1, address 0x10
            self.board = Board(1, 0x10)
            
            # Initialize board and check status
            retry_count = 0
            max_retries = 5
            
            while self.board.begin() != self.board.STA_OK and retry_count < max_retries:
                print(f"DFRobot HAT initialization attempt {retry_count + 1}/{max_retries}...")
                time.sleep(1)
                retry_count += 1
            
            if retry_count >= max_retries:
                print("Failed to initialize DFRobot HAT after maximum retries")
                self.board = None
                self.servo = None
                return
            
            # Initialize servo control
            self.servo = Servo(self.board)
            self.servo.begin()
            
            print(f"DFRobot HAT servo initialized successfully on PWM {self.servo_pin}")
            
        except ImportError as e:
            print(f"DFRobot library not found: {e}")
            print("Please install: https://github.com/DFRobot/DFRobot_RaspberryPi_Expansion_Board")
            self.board = None
            self.servo = None
        except Exception as e:
            print(f"Failed to initialize DFRobot HAT: {e}")
            self.board = None
            self.servo = None

    def set_angle(self, angle):
        """Set servo to specified angle (0-180 degrees)"""
        if not self.servo or not self.board:
            print("DFRobot HAT servo not initialized")
            return False
        
        try:
            # Clamp angle to valid range (0-180)
            angle = max(0, min(180, angle))
            
            # Use DFRobot servo.move() function
            # First parameter: PWM channel (0 = PWM0)
            # Second parameter: angle in degrees (0-180)
            self.servo.move(self.servo_pin, angle)
            
            time.sleep(self.servo_settle_time)
            print(f"DFRobot HAT servo set to {angle} degrees on PWM {self.servo_pin}")
            return True
            
        except Exception as e:
            print(f"Error setting DFRobot HAT servo angle: {e}")
            return False

    def door_open(self):
        """Open door, wait, then close it"""
        if not self.servo or not self.board:
            print("Cannot open door - DFRobot HAT servo not initialized")
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
        """Get current servo position (returns last set position)"""
        if not self.servo:
            return None
        
        # DFRobot HAT doesn't provide position feedback
        # Return last known position based on recent commands
        return getattr(self, '_last_position', self.servo_closed_position)

    def test_servo(self):
        """Test servo movement through full range"""
        if not self.servo or not self.board:
            print("Cannot test servo - DFRobot HAT not initialized")
            return False
        
        print("Testing DFRobot HAT servo movement...")
        
        # Test positions
        test_positions = [0, 45, 90, 135, 180, 90, 0]
        
        for position in test_positions:
            print(f"Moving to {position} degrees...")
            self.set_angle(position)
            time.sleep(1)
        
        print("DFRobot HAT servo test completed")
        return True

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
        if self.servo and self.board:
            try:
                # Return to closed position before cleanup
                print("Returning DFRobot HAT servo to closed position...")
                self.set_angle(self.servo_closed_position)
                time.sleep(self.servo_settle_time)
                print("DFRobot HAT servo returned to closed position")
            except Exception as e:
                print(f"Error during DFRobot HAT servo cleanup: {e}")
            
        print("DFRobot HAT servo resources cleaned up")

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup"""
        self.cleanup()

# Signal handler for graceful shutdown
def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    print(f"\nReceived signal {sig}, shutting down DFRobot HAT servo manager...")
    sys.exit(0)

def main():
    """Main function for testing DFRobot HAT servo manager"""
    print("Starting DFRobot HAT Servo Manager Test...")
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        with ServoManager() as servo_manager:
            if not servo_manager.servo:
                print("Failed to initialize DFRobot HAT servo. Exiting...")
                return
            
            print("DFRobot HAT Servo Manager initialized successfully")
            print("Configuration:")
            print(f"  PWM Channel: {servo_manager.servo_pin}")
            print(f"  Open Position: {servo_manager.servo_open_position}°")
            print(f"  Closed Position: {servo_manager.servo_closed_position}°")
            print(f"  Door Open Duration: {servo_manager.door_open_duration}s")
            
            # Test servo functionality
            print("\nTesting servo functionality...")
            servo_manager.test_servo()
            
            print("\nTesting door open/close cycle...")
            servo_manager.door_open()
            
            print("\nDFRobot HAT servo manager ready. Press Ctrl+C to exit.")
            
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
                    print(f"Last set servo position: {position}°")
                elif user_input in ['quit', 'exit', 'q']:
                    break
                else:
                    print("Available commands: open, close, test, status, quit")
                    
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        print("DFRobot HAT servo manager shutdown complete")

if __name__ == '__main__':
    main()
