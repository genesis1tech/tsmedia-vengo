#!/usr/bin/env python3
"""
Enhanced Servo Controller for TSV6 Raspberry Pi
Controls servo motors using PCA9685 PWM HAT with improved error handling and diagnostics
"""

import time
import threading
from typing import Optional, Dict, Any
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import board
    import busio
    from adafruit_pca9685 import PCA9685
    PWM_HAT_AVAILABLE = True
    logger.info("✅ PCA9685 libraries loaded successfully")
except ImportError as e:
    PWM_HAT_AVAILABLE = False
    logger.warning(f"⚠ PCA9685 libraries not available - servo control will be simulated: {e}")

class EnhancedServoController:
    """Enhanced servo controller with better diagnostics and error handling"""
    
    def __init__(self, channel: int = 0, i2c_address: int = 0x40):
        """
        Initialize enhanced servo controller
        
        Args:
            channel: PWM channel on PCA9685 (0-15, default: 0)
            i2c_address: I2C address of PCA9685 (default: 0x40)
        """
        self.channel = channel
        self.i2c_address = i2c_address
        self.current_position = 0
        self.pca: Optional[PCA9685] = None
        self.i2c: Optional[busio.I2C] = None
        self.is_moving = False
        self.lock = threading.Lock()
        self.initialization_error = None
        
        # Servo parameters for PCA9685 (16-bit resolution)
        self.min_pulse = 3277   # 1ms pulse width (0 degrees)
        self.max_pulse = 6553   # 2ms pulse width (180 degrees)
        self.pwm_frequency = 50  # 50Hz for servos
        
        # Initialize PWM HAT if available
        if PWM_HAT_AVAILABLE:
            self._initialize_pwm_hat()
        else:
            logger.info(f"✓ Servo simulation initialized on channel {channel}")
        
    def _initialize_pwm_hat(self) -> bool:
        """Initialize PCA9685 PWM HAT with comprehensive error handling"""
        try:
            logger.info(f"🔧 Initializing PCA9685 PWM HAT on channel {self.channel}")
            
            # Initialize I2C bus
            self.i2c = busio.I2C(board.SCL, board.SDA)
            logger.info("✅ I2C bus initialized")
            
            # Initialize PCA9685 with specific address
            self.pca = PCA9685(self.i2c, address=self.i2c_address)
            logger.info(f"✅ PCA9685 initialized at address 0x{self.i2c_address:02x}")
            
            # Set frequency
            self.pca.frequency = self.pwm_frequency
            logger.info(f"✅ PWM frequency set to {self.pwm_frequency}Hz")
            
            # Move to initial closed position (0 degrees)
            self._set_angle(0)
            time.sleep(0.1)  # Minimal delay for servo to reach position
            self._stop_pwm()  # Stop signal to prevent jitter
            
            logger.info(f"✅ PCA9685 PWM HAT fully initialized on channel {self.channel}")
            return True
            
        except Exception as e:
            self.initialization_error = str(e)
            logger.error(f"❌ Failed to initialize PWM HAT: {e}")
            self.pca = None
            self.i2c = None
            return False
    
    def get_diagnostics(self) -> Dict[str, Any]:
        """Get comprehensive diagnostic information"""
        diagnostics = {
            "pwm_hat_available": PWM_HAT_AVAILABLE,
            "initialized": self.pca is not None,
            "initialization_error": self.initialization_error,
            "channel": self.channel,
            "i2c_address": f"0x{self.i2c_address:02x}",
            "current_position": self.current_position,
            "is_moving": self.is_moving,
            "pwm_frequency": self.pwm_frequency,
            "pulse_range": f"{self.min_pulse}-{self.max_pulse}",
        }
        
        if PWM_HAT_AVAILABLE and self.pca:
            try:
                # Test basic communication
                current_freq = self.pca.frequency
                diagnostics["actual_frequency"] = current_freq
                diagnostics["communication_ok"] = True
            except Exception as e:
                diagnostics["communication_ok"] = False
                diagnostics["communication_error"] = str(e)
        
        return diagnostics
    
    def _calculate_pulse_width(self, angle: int) -> int:
        """Calculate pulse width for given angle with validation"""
        # Validate angle range
        if angle < 0 or angle > 180:
            raise ValueError(f"Angle {angle} out of range (0-180)")
        
        # Map angle (0-180) to pulse width (min_pulse-max_pulse)
        pulse_width = int(self.min_pulse + (angle / 180.0) * (self.max_pulse - self.min_pulse))
        logger.debug(f"Angle {angle}° -> Pulse width {pulse_width}")
        return pulse_width
    
    def _set_angle(self, angle: int) -> bool:
        """Set servo to specific angle with error handling"""
        try:
            if not self.pca:
                logger.warning("Cannot set angle - PWM HAT not initialized")
                return False
            
            pulse_width = self._calculate_pulse_width(angle)
            self.pca.channels[self.channel].duty_cycle = pulse_width
            logger.debug(f"Set channel {self.channel} to pulse width {pulse_width} ({angle}°)")
            return True
            
        except Exception as e:
            logger.error(f"Error setting servo angle: {e}")
            return False
    
    def _stop_pwm(self) -> bool:
        """Stop PWM signal to prevent servo jitter"""
        try:
            if self.pca:
                self.pca.channels[self.channel].duty_cycle = 0
                logger.debug(f"Stopped PWM on channel {self.channel}")
                return True
        except Exception as e:
            logger.error(f"Error stopping PWM: {e}")
        return False
    
    def move_to_angle(self, target_angle: int, validate_move: bool = True) -> bool:
        """
        Move servo to target angle with validation
        
        Args:
            target_angle: Target angle (0-180 degrees)
            validate_move: Whether to validate the move was successful
        
        Returns:
            bool: True if successful, False otherwise
        """
        with self.lock:
            if self.is_moving:
                logger.warning("⚠ Servo is already moving")
                return False
            
            # Clamp angle to valid range
            original_angle = target_angle
            target_angle = max(0, min(180, target_angle))
            
            if original_angle != target_angle:
                logger.warning(f"Angle clamped from {original_angle}° to {target_angle}°")
            
            if target_angle == self.current_position:
                logger.info(f"Servo already at {target_angle}°")
                return True
            
            self.is_moving = True
        
        success = False
        try:
            logger.info(f"🔄 Moving servo from {self.current_position}° to {target_angle}°")
            
            if PWM_HAT_AVAILABLE and self.pca:
                # Move to target angle
                if self._set_angle(target_angle):
                    time.sleep(0.2)  # Time for servo to physically move
                    self._stop_pwm()
                    success = True
                    logger.info(f"✅ Servo moved to {target_angle}°")
                else:
                    logger.error(f"❌ Failed to move servo to {target_angle}°")
            else:
                # Simulation mode
                logger.info(f"[SIMULATED] Servo moving to {target_angle}°")
                time.sleep(0.2)
                success = True
            
            if success:
                self.current_position = target_angle
            
        except Exception as e:
            logger.error(f"❌ Error moving servo: {e}")
        finally:
            with self.lock:
                self.is_moving = False
        
        return success
    
    def open_door(self) -> bool:
        """
        Execute door open sequence with error handling
        Returns True if successful, False otherwise
        """
        logger.info("🚪 Starting door open sequence...")
        
        def door_sequence():
            try:
                # Move from 0 to 90 degrees (open)
                if not self.move_to_angle(90):
                    logger.error("❌ Failed to open door")
                    return False
                
                logger.info("⏳ Door open - waiting 2.5 seconds...")
                time.sleep(2.5)
                
                # Move from 90 to 0 degrees (close)
                logger.info("🚪 Closing platter door...")
                if not self.move_to_angle(0):
                    logger.error("❌ Failed to close door")
                    return False
                
                logger.info("✅ Door sequence completed successfully")
                return True
                
            except Exception as e:
                logger.error(f"❌ Error in door sequence: {e}")
                return False
        
        # Run door sequence in separate thread
        door_thread = threading.Thread(target=door_sequence)
        door_thread.daemon = True
        door_thread.start()
        
        return True  # Thread started successfully
    
    def test_servo_range(self) -> bool:
        """Test servo across its full range"""
        logger.info("🔧 Testing servo range...")
        
        test_angles = [0, 45, 90, 135, 180, 90, 45, 0]
        
        for angle in test_angles:
            if not self.move_to_angle(angle):
                logger.error(f"❌ Failed to move to {angle}°")
                return False
            time.sleep(1)
        
        logger.info("✅ Servo range test completed")
        return True
    
    def cleanup(self):
        """Cleanup PWM HAT resources with error handling"""
        if PWM_HAT_AVAILABLE and self.pca:
            try:
                # Return to closed position
                logger.info("🔄 Returning servo to closed position...")
                self._set_angle(0)
                time.sleep(0.1)
                
                # Stop PWM signal
                self._stop_pwm()
                
                # Deinitialize PCA9685
                self.pca.deinit()
                logger.info("✅ PWM HAT cleaned up successfully")
                
            except Exception as e:
                logger.warning(f"⚠ Error during PWM HAT cleanup: {e}")
        
        # Reset state
        self.pca = None
        self.i2c = None

def main():
    """Test the enhanced servo controller"""
    logger.info("=== Enhanced Servo Controller Test ===")
    
    servo = EnhancedServoController(channel=0)
    
    try:
        # Print diagnostics
        diagnostics = servo.get_diagnostics()
        logger.info("🔧 Servo Diagnostics:")
        for key, value in diagnostics.items():
            logger.info(f"  {key}: {value}")
        
        if diagnostics['initialized']:
            # Test servo functionality
            logger.info("\n🔧 Testing servo functionality...")
            servo.test_servo_range()
            
            logger.info("\n🚪 Testing door sequence...")
            servo.open_door()
            time.sleep(4)  # Wait for sequence to complete
            
        else:
            logger.error("❌ Servo not initialized - cannot run tests")
            
    except KeyboardInterrupt:
        logger.info("\n⚠ Test interrupted by user")
    except Exception as e:
        logger.error(f"❌ Test error: {e}")
    finally:
        servo.cleanup()
        logger.info("🔧 Test completed")

if __name__ == "__main__":
    main()
