#!/usr/bin/env python3
"""
Simple Servo Controller for TSV6 Raspberry Pi
Controls servo motors using GPIO 18 directly with RPi.GPIO
"""

import time
import threading
from typing import Optional

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("⚠ RPi.GPIO not available - servo control will be simulated")

class SimpleServoController:
    """Controls servo motors via GPIO 18 for door opening mechanism"""
    
    def __init__(self, gpio_pin: int = 18):
        """
        Initialize servo controller with GPIO pin
        
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
        self.pwm_frequency = 50  # 50Hz for standard servos
        
        # PWM duty cycle calculations for standard servo
        # 0.5ms pulse (2.5% duty) = 0 degrees
        # 1.5ms pulse (7.5% duty) = 90 degrees  
        # 2.5ms pulse (12.5% duty) = 180 degrees
        self.min_duty = 2.5
        self.max_duty = 12.5
        
        # Initialize GPIO
        self.pwm = None
        if GPIO_AVAILABLE:
            self._initialize_gpio()
        else:
            print(f"✓ Servo simulation initialized on GPIO {gpio_pin}")
        
    def _initialize_gpio(self):
        """Initialize GPIO for servo control"""
        try:
            # Set GPIO mode to BCM
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            
            # Set GPIO pin as output
            GPIO.setup(self.gpio_pin, GPIO.OUT)
            
            # Initialize PWM
            self.pwm = GPIO.PWM(self.gpio_pin, self.pwm_frequency)
            self.pwm.start(0)  # Start with 0% duty cycle
            
            # Move to initial closed position (0 degrees)
            self._set_angle(0)
            time.sleep(0.5)  # Allow time for servo to reach position
            
            print(f"✅ GPIO {self.gpio_pin} initialized for servo control")
            
        except Exception as e:
            print(f"❌ Failed to initialize GPIO {self.gpio_pin}: {e}")
            self.pwm = None
            raise
            
    def _angle_to_duty(self, angle: int) -> float:
        """Convert angle to duty cycle percentage"""
        # Clamp angle to valid range
        angle = max(self.min_angle, min(self.max_angle, angle))
        
        # Linear interpolation between min and max duty
        duty_range = self.max_duty - self.min_duty
        angle_range = self.max_angle - self.min_angle
        
        duty = self.min_duty + (angle / angle_range) * duty_range
        return duty
        
    def _set_angle(self, angle: int):
        """Set servo to specific angle (0-180 degrees)"""
        if not self.pwm:
            print(f"🎭 Simulated: Setting servo to {angle}°")
            self.current_position = angle
            return
            
        # Clamp angle to valid range
        angle = max(self.min_angle, min(self.max_angle, angle))
        
        try:
            # Calculate duty cycle
            duty = self._angle_to_duty(angle)
            print(f"⚡ Setting servo to {angle}° (duty: {duty:.2f}%)")
            
            # Set PWM duty cycle
            self.pwm.ChangeDutyCycle(duty)
            self.current_position = angle
            
            # Keep signal active long enough for servo to move
            time.sleep(0.5)  # Give servo time to move
            
            # Stop PWM signal to prevent jitter
            self.pwm.ChangeDutyCycle(0)
            
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
        """Test servo movement: 0° -> 90° -> 180° -> 0°"""
        print("🔧 Testing servo movement...")
        
        try:
            print("  Testing full range movement...")
            
            # Test extreme positions to guarantee movement
            print("  Moving to 0° (closed)...")
            self._set_angle(0)
            time.sleep(2)
            
            print("  Moving to 180° (fully open)...")
            self._set_angle(180)
            time.sleep(2)
            
            print("  Moving to 90° (middle)...")
            self._set_angle(90)
            time.sleep(2)
            
            print("  Moving back to 0° (closed)...")
            self._set_angle(0)
            time.sleep(1)
            
            print("✅ Servo test complete")
            
        except Exception as e:
            print(f"❌ Servo test failed: {e}")
    
    def cleanup(self):
        """Cleanup resources and stop servo"""
        with self.lock:
            if self.pwm:
                try:
                    # Return to closed position
                    print("🧹 Cleaning up servo controller...")
                    self._set_angle(0)
                    time.sleep(0.5)
                    
                    # Stop PWM
                    self.pwm.stop()
                    
                    # Clean up GPIO
                    GPIO.cleanup()
                    
                    print("✅ Servo controller cleanup complete")
                    
                except Exception as e:
                    print(f"⚠ Warning during cleanup: {e}")


def main():
    """Test the simple servo controller"""
    print("Testing Simple GPIO Servo Controller...")
    
    try:
        servo = SimpleServoController(gpio_pin=18)
        
        # Test simple movement: 0 -> 90 -> 0
        print("\n1. Testing door open to 90°...")
        servo.open_door(angle=90, hold_time=2.0)
        
        print("\n2. Testing door close to 0°...")
        servo.close_door(hold_time=1.0)
        
        print("\n3. Testing quick movement...")
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
