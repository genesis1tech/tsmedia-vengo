#!/usr/bin/env python3
"""
Servo Controller for TSV6 Raspberry Pi
Controls servo motors using DFRobot I/O Expansion HAT
"""

import time
import threading
from typing import Optional

try:
    from DFRobot_RaspberryPi_Expansion_Board import DFRobot_Expansion_Board_IIC as Board
    from DFRobot_RaspberryPi_Expansion_Board import DFRobot_Expansion_Board_Servo as Servo
    DFROBOT_HAT_AVAILABLE = True
except ImportError:
    DFROBOT_HAT_AVAILABLE = False
    print("⚠ DFRobot libraries not available - servo control will be simulated")

class ServoController:
    """Controls servo motors via DFRobot I/O Expansion HAT for door opening mechanism"""
    
    def __init__(self, channel: int = 0):
        """
        Initialize servo controller with DFRobot HAT
        
        Args:
            channel: PWM channel on DFRobot HAT (0-7, default: 0)
        """
        self.channel = channel
        self.current_position = 0
        self.board: Optional[Board] = None
        self.servo: Optional[Servo] = None
        self.is_moving = False
        self.lock = threading.Lock()
        
        # Servo parameters for DFRobot HAT
        self.min_angle = 0      # Minimum servo angle (degrees)
        self.max_angle = 180    # Maximum servo angle (degrees)
        
        # Initialize DFRobot HAT if available
        if DFROBOT_HAT_AVAILABLE:
            self._initialize_dfrobot_hat()
        else:
            print(f"✓ Servo simulation initialized on channel {channel}")
        
    def _initialize_dfrobot_hat(self):
        """Initialize DFRobot I/O Expansion HAT"""
        try:
            # Initialize DFRobot board (bus 1, address 0x10)
            self.board = Board(1, 0x10)
            
            # Board begin and check status
            max_retries = 3
            for retry in range(max_retries):
                if self.board.begin() == self.board.STA_OK:
                    break
                print(f"🔄 Board initialization attempt {retry + 1}/{max_retries}...")
                time.sleep(1)
            else:
                raise Exception("Board initialization failed after retries")
            
            # Initialize servo controller
            self.servo = Servo(self.board)
            self.servo.begin()
            
            # Move to initial closed position (0 degrees)
            self._set_angle(0)
            time.sleep(0.5)  # Allow time for servo to reach position
            
            print(f"✅ DFRobot I/O Expansion HAT initialized on channel {self.channel}")
            
        except Exception as e:
            print(f"❌ Failed to initialize DFRobot HAT: {e}")
            self.board = None
            self.servo = None
            raise
            
    def _print_board_status(self):
        """Print board status for debugging"""
        if not self.board:
            return
            
        if self.board.last_operate_status == self.board.STA_OK:
            print("Board status: OK")
        elif self.board.last_operate_status == self.board.STA_ERR:
            print("Board status: unexpected error")
        elif self.board.last_operate_status == self.board.STA_ERR_DEVICE_NOT_DETECTED:
            print("Board status: device not detected")
        elif self.board.last_operate_status == self.board.STA_ERR_PARAMETER:
            print("Board status: parameter error")
        elif self.board.last_operate_status == self.board.STA_ERR_SOFT_VERSION:
            print("Board status: unsupported firmware version")
            
    def _set_angle(self, angle: int):
        """Set servo to specific angle (0-180 degrees)"""
        if not self.servo:
            print(f"🎭 Simulated: Setting servo to {angle}°")
            self.current_position = angle
            return
            
        # Clamp angle to valid range
        angle = max(self.min_angle, min(self.max_angle, angle))
        
        try:
            # Move servo to specified angle
            self.servo.move(self.channel, angle)
            self.current_position = angle
            
            # Check status
            if self.board.last_operate_status != self.board.STA_OK:
                self._print_board_status()
            
        except Exception as e:
            print(f"❌ Error setting servo angle: {e}")
            self._print_board_status()
            raise
    
    def open_door(self, angle: int = 90, hold_time: float = 2.0):
        """
        Open door by rotating servo to specified angle
        
        Args:
            angle: Target angle in degrees (0-180, default: 90)
            hold_time: Time to hold position in seconds (default: 2.0)
        """
        with self.lock:
            if self.is_moving:
                print("⚠ Servo already moving, ignoring command")
                return False
                
            self.is_moving = True
            
            try:
                print(f"🚪 Opening door: servo to {angle}°")
                self._set_angle(angle)
                
                # Hold position
                if hold_time > 0:
                    time.sleep(hold_time)
                
                return True
                
            except Exception as e:
                print(f"❌ Failed to open door: {e}")
                return False
                
            finally:
                self.is_moving = False
    
    def close_door(self, hold_time: float = 1.0):
        """
        Close door by returning servo to 0 degrees
        
        Args:
            hold_time: Time to hold closed position in seconds (default: 1.0)
        """
        with self.lock:
            if self.is_moving:
                print("⚠ Servo already moving, ignoring command")
                return False
                
            self.is_moving = True
            
            try:
                print("🚪 Closing door: servo to 0°")
                self._set_angle(0)
                
                # Hold position briefly
                if hold_time > 0:
                    time.sleep(hold_time)
                
                return True
                
            except Exception as e:
                print(f"❌ Failed to close door: {e}")
                return False
                
            finally:
                self.is_moving = False
    
    def get_position(self) -> int:
        """Get current servo position in degrees"""
        return self.current_position
    
    def is_door_open(self) -> bool:
        """Check if door is in open position"""
        return self.current_position > 10  # Consider open if > 10 degrees
    
    def test_movement(self):
        """Test servo movement for debugging"""
        print("🔧 Testing servo movement...")
        
        # Test sequence: 0° -> 90° -> 180° -> 0°
        test_positions = [0, 90, 180, 0]
        
        for position in test_positions:
            print(f"  Moving to {position}°...")
            self._set_angle(position)
            time.sleep(1.5)
        
        print("✅ Servo test complete")
    
    def cleanup(self):
        """Cleanup resources and stop servo"""
        with self.lock:
            if self.servo:
                try:
                    # Return to closed position
                    print("🧹 Cleaning up servo controller...")
                    self._set_angle(0)
                    time.sleep(0.5)
                    
                    print("✅ Servo controller cleanup complete")
                    
                except Exception as e:
                    print(f"⚠ Warning during cleanup: {e}")


def main():
    """Test the servo controller"""
    print("Testing DFRobot Servo Controller...")
    
    try:
        servo = ServoController(channel=0)
        
        # Test basic movements
        print("\n1. Testing door open...")
        servo.open_door(angle=90, hold_time=2.0)
        
        print("\n2. Testing door close...")
        servo.close_door(hold_time=1.0)
        
        print("\n3. Testing full range movement...")
        servo.test_movement()
        
        print(f"\n4. Final position: {servo.get_position()}°")
        print(f"5. Door open status: {servo.is_door_open()}")
        
        # Cleanup
        servo.cleanup()
        
        print("\n✅ All tests completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    return True


if __name__ == "__main__":
    main()
