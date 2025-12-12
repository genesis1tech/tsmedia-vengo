#!/usr/bin/env python3
"""
Pigpio-based Servo Controller for TSV6 Raspberry Pi
Controls servo motors using GPIO 18 with pigpio daemon for jitter-free operation
"""

import time
import threading
from typing import Optional

try:
    import pigpio
    PIGPIO_AVAILABLE = True
except ImportError:
    PIGPIO_AVAILABLE = False
    print("⚠ pigpio not available - servo control will be simulated")

class PigpioServoController:
    """Controls servo motors via pigpio daemon for jitter-free door opening mechanism"""
    
    def __init__(self, gpio_pin: int = 18):
        """
        Initialize servo controller with pigpio
        
        Args:
            gpio_pin: GPIO pin number (default: 18)
        """
        self.gpio_pin = gpio_pin
        self.current_position = 0
        self.is_moving = False
        self.lock = threading.Lock()
        
        # Servo parameters
        self.min_angle = 0      # Minimum servo angle (degrees)
        self.max_angle = 180    # Maximum servo angle (degrees)
        
        # Pulse width values in microseconds (pigpio uses microseconds)
        # Standard servo: 500µs = 0°, 1500µs = 90°, 2500µs = 180°
        self.min_pulse_width = 500   # 0.5ms for 0 degrees
        self.max_pulse_width = 2500  # 2.5ms for 180 degrees
        
        # Initialize pigpio
        self.pi = None
        if PIGPIO_AVAILABLE:
            self._initialize_pigpio()
        else:
            print(f"✓ Servo simulation initialized on GPIO {gpio_pin}")
        
    def _initialize_pigpio(self):
        """Initialize pigpio connection"""
        try:
            # Connect to pigpio daemon
            self.pi = pigpio.pi()
            
            if not self.pi.connected:
                raise Exception("Failed to connect to pigpio daemon")
            
            # Set GPIO pin as servo output
            self.pi.set_mode(self.gpio_pin, pigpio.OUTPUT)
            
            # Start with NO pulses at rest position (0 degrees)
            # Pulses will only be sent during open_door/close_door sequences
            self.pi.set_servo_pulsewidth(self.gpio_pin, 0)
            self.current_position = 0
            
            print(f"✅ Pigpio initialized on GPIO {self.gpio_pin} (no pulses at rest)")
            
        except Exception as e:
            print(f"❌ Failed to initialize pigpio: {e}")
            self.pi = None
            raise
            
    def _angle_to_pulse_width(self, angle: int) -> int:
        """Convert angle to pulse width in microseconds"""
        # Clamp angle to valid range
        angle = max(self.min_angle, min(self.max_angle, angle))
        
        # Linear interpolation between min and max pulse width
        pulse_range = self.max_pulse_width - self.min_pulse_width
        angle_range = self.max_angle - self.min_angle
        
        pulse_width = self.min_pulse_width + (angle / angle_range) * pulse_range
        return int(pulse_width)
        
    def _set_angle(self, angle: int):
        """Set servo to specific angle (0-180 degrees)"""
        if not self.pi:
            print(f"🎭 Simulated: Setting servo to {angle}°")
            self.current_position = angle
            return
            
        # Clamp angle to valid range
        angle = max(self.min_angle, min(self.max_angle, angle))
        
        try:
            # Calculate pulse width
            pulse_width = self._angle_to_pulse_width(angle)
            print(f"⚡ Setting servo to {angle}° (pulse: {pulse_width}µs)")

            # Set servo pulse width
            self.pi.set_servo_pulsewidth(self.gpio_pin, pulse_width)
            self.current_position = angle

            # Give servo time to move (1.5s ensures movement completion under load)
            # Standard servos need 0.45-1.5s to move 90° under door load
            time.sleep(1.5)

            # Auto-disable pulses at position 0 to prevent jitter
            if angle == 0:
                self.pi.set_servo_pulsewidth(self.gpio_pin, 0)
                print(f"💤 Auto-disabled pulses at position 0 (prevents jitter)")
            
        except Exception as e:
            print(f"❌ Error setting servo angle: {e}")
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
                self._set_angle(0)  # Pulses auto-disabled at position 0
                
                # Hold position briefly (no pulses needed at position 0)
                if hold_time > 0:
                    time.sleep(hold_time)
                
                # Disable pulses when at rest position (0°) to prevent unnecessary pulsing
                self.disable_servo()
                print("💤 Servo at rest (0°) - pulses disabled")
                
                return True
                
            except Exception as e:
                print(f"❌ Failed to close door: {e}")
                return False
                
            finally:
                self.is_moving = False
    
    def disable_servo(self):
        """Disable servo pulses to prevent jitter while keeping controller active"""
        if not self.pi:
            print("🎭 Simulated: Disabling servo pulses")
            return
        
        try:
            # Disable servo pulses (set to 0)
            self.pi.set_servo_pulsewidth(self.gpio_pin, 0)
            print(f"💤 Servo pulses disabled on GPIO {self.gpio_pin} (prevents jitter)")
        except Exception as e:
            print(f"⚠ Error disabling servo: {e}")
    
    def get_position(self) -> int:
        """Get current servo position in degrees"""
        return self.current_position
    
    def is_door_open(self) -> bool:
        """Check if door is in open position"""
        return self.current_position > 10  # Consider open if > 10 degrees
    
    def test_movement(self):
        """Test servo movement: 0° -> 90° -> 180° -> 0°"""
        print("🔧 Testing servo movement...")
        
        try:
            print("  Testing full range movement...")
            
            # Test positions for quick, effective movement
            test_positions = [
                (0, "closed"),
                (90, "middle"), 
                (180, "fully open"),
                (90, "middle"),
                (0, "closed")
            ]
            
            for angle, description in test_positions:
                print(f"  Moving to {angle}° ({description})...")
                self._set_angle(angle)
                time.sleep(1)
            
            print("✅ Servo test complete")
            
        except Exception as e:
            print(f"❌ Servo test failed: {e}")
    
    def cleanup(self):
        """Cleanup resources and stop servo"""
        with self.lock:
            if self.pi:
                try:
                    # Return to closed position (pulses auto-disabled at position 0)
                    print("🧹 Cleaning up servo controller...")
                    self._set_angle(0)
                    
                    # Disconnect from pigpio daemon
                    self.pi.stop()
                    
                    print("✅ Servo controller cleanup complete")
                    
                except Exception as e:
                    print(f"⚠ Warning during cleanup: {e}")


def main():
    """Test the pigpio servo controller"""
    print("Testing Pigpio Servo Controller...")
    
    try:
        servo = PigpioServoController(gpio_pin=18)
        
        # Test quick movement: 0 -> 90 -> 0
        print("\n1. Testing door open to 90°...")
        servo.open_door(angle=90, hold_time=2.0)
        
        print("\n2. Testing door close to 0°...")
        servo.close_door(hold_time=1.0)
        
        print("\n3. Testing quick effective movement...")
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
