#!/usr/bin/env python3
"""
STServo Controller for TSV6 Raspberry Pi

Controls Waveshare ST3020 bus servos via USB serial adapter (Bus Servo Adapter A).
Provides the same interface as the legacy PigpioServoController.
"""

import os
import time
import threading
import logging
from typing import Optional
from pathlib import Path

# Add vendor directory to Python path for scservo_sdk imports
import sys
_vendor_path = Path(__file__).parent / 'vendor'
if str(_vendor_path) not in sys.path:
    sys.path.insert(0, str(_vendor_path))

try:
    from scservo_sdk import PortHandler, sms_sts, SMS_STS_TORQUE_ENABLE
    STSERVO_AVAILABLE = True
except ImportError as e:
    STSERVO_AVAILABLE = False
    print(f"STServo SDK not available: {e}")


logger = logging.getLogger(__name__)


class STServoController:
    """
    Controls ST3020 bus servo via USB serial adapter.

    Position mapping:
        - 0 degrees = position 0
        - 120 degrees = position 1365
        - 360 degrees = position 4095

    The servo supports 0.088 degree resolution (4096 steps per rotation).
    """

    # Position conversion: 4096 steps per 360 degrees
    STEPS_PER_DEGREE = 4096.0 / 360.0  # ~11.378 steps/degree

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 115200,
        servo_id: int = 1,
        open_position: int = 1365,      # 120 degrees
        closed_position: int = 0,       # 0 degrees
        moving_speed: int = 0,          # 0 = maximum speed
        acceleration: int = 50,         # Acceleration value
        timeout: float = 1.0,
    ):
        """
        Initialize STServo controller.

        Args:
            port: Serial port path (auto-detected if None)
            baudrate: Serial baud rate (default: 115200)
            servo_id: Servo ID on the bus (default: 1)
            open_position: Position value for open door (default: 1365 = 120 degrees)
            closed_position: Position value for closed door (default: 0)
            moving_speed: Speed setting (0 = maximum, default: 0)
            acceleration: Acceleration value (default: 50)
            timeout: Command timeout in seconds
        """
        # Load from environment variables if not specified
        self.port = port or os.environ.get('TSV6_SERVO_PORT') or self._auto_detect_port()
        self.baudrate = int(os.environ.get('TSV6_SERVO_BAUD', baudrate))
        self.servo_id = int(os.environ.get('TSV6_SERVO_ID', servo_id))
        self.open_position = int(os.environ.get('TSV6_SERVO_OPEN_POS', open_position))
        self.closed_position = int(os.environ.get('TSV6_SERVO_CLOSED_POS', closed_position))
        self.moving_speed = int(os.environ.get('TSV6_SERVO_SPEED', moving_speed))
        self.acceleration = acceleration
        self.timeout = timeout

        self.current_position = 0
        self.is_moving = False
        self.lock = threading.Lock()

        self.port_handler: Optional[PortHandler] = None
        self.servo: Optional[sms_sts] = None
        self._connected = False

        if not STSERVO_AVAILABLE:
            logger.warning("STServo SDK not available - running in simulation mode")
            print("STServo SDK not available - running in simulation mode")
            return

        self._connect()

    def _auto_detect_port(self) -> str:
        """Auto-detect the serial port for the USB adapter."""
        # Common port paths for USB serial adapters on Linux
        ports = [
            '/dev/ttyUSB0',
            '/dev/ttyUSB1',
            '/dev/ttyACM0',
            '/dev/ttyACM1',
            '/dev/tsv6-servo',  # Custom udev symlink if configured
        ]

        for port in ports:
            if os.path.exists(port):
                logger.info(f"Auto-detected serial port: {port}")
                return port

        # Fallback to default
        logger.warning("No serial port auto-detected, using /dev/ttyUSB0")
        return '/dev/ttyUSB0'

    def _connect(self) -> bool:
        """Connect to the servo via serial port."""
        if not STSERVO_AVAILABLE:
            return False

        try:
            logger.info(f"Connecting to STServo on {self.port} at {self.baudrate} baud...")

            self.port_handler = PortHandler(self.port)
            self.port_handler.baudrate = self.baudrate

            if not self.port_handler.openPort():
                logger.error(f"Failed to open port {self.port}")
                return False

            self.servo = sms_sts(self.port_handler)
            self._connected = True

            # Enable torque
            self._enable_torque(True)

            logger.info(f"STServo connected on {self.port} (ID: {self.servo_id})")
            print(f"STServo connected on {self.port} (ID: {self.servo_id})")

            return True

        except Exception as e:
            logger.error(f"Failed to connect to STServo: {e}")
            self._connected = False
            return False

    def _enable_torque(self, enable: bool) -> bool:
        """Enable or disable servo torque."""
        if not self._connected or not self.servo:
            return False

        try:
            result = self.servo.write1ByteTxRx(
                self.servo_id,
                SMS_STS_TORQUE_ENABLE,
                1 if enable else 0
            )
            logger.debug(f"Torque {'enabled' if enable else 'disabled'}")
            return True
        except Exception as e:
            logger.error(f"Failed to set torque: {e}")
            return False

    def _set_position(self, position: int) -> bool:
        """
        Set servo to specific position.

        Args:
            position: Target position (0-4095)

        Returns:
            True if successful
        """
        if not self._connected or not self.servo:
            # Simulation mode
            logger.info(f"[SIM] Moving servo to position {position}")
            print(f"[SIM] Moving servo to position {position}")
            self.current_position = position
            return True

        try:
            # Clamp position to valid range
            position = max(0, min(4095, position))

            # WritePosEx(id, position, speed, acceleration)
            result = self.servo.WritePosEx(
                self.servo_id,
                position,
                self.moving_speed,
                self.acceleration
            )

            self.current_position = position
            logger.debug(f"Servo moved to position {position}")
            return True

        except Exception as e:
            logger.error(f"Failed to set servo position: {e}")
            return False

    def _set_angle(self, angle: float) -> bool:
        """
        Set servo to specific angle in degrees.

        Args:
            angle: Target angle (0-360 degrees)

        Returns:
            True if successful
        """
        # Convert degrees to position
        position = int(angle * self.STEPS_PER_DEGREE)
        return self._set_position(position)

    def _wait_for_movement(self, timeout: float = 2.0) -> bool:
        """Wait for servo to complete movement."""
        if not self._connected or not self.servo:
            # Simulation mode - just wait a short time
            time.sleep(0.3)
            return True

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                moving, _, _ = self.servo.ReadMoving(self.servo_id)
                if not moving:
                    return True
                time.sleep(0.05)
            except Exception:
                break

        return False

    def open_door(self, angle: Optional[int] = None, hold_time: float = 3.0) -> bool:
        """
        Open door by moving servo to open position.

        Args:
            angle: Target angle in degrees (uses open_position if None)
            hold_time: Time to hold position in seconds

        Returns:
            True if successful
        """
        with self.lock:
            if self.is_moving:
                logger.warning("Servo already moving, ignoring command")
                return False

            self.is_moving = True

            try:
                if angle is not None:
                    target = int(angle * self.STEPS_PER_DEGREE)
                else:
                    target = self.open_position

                logger.info(f"Opening door: moving to position {target}")
                print(f"Opening door: moving to position {target}")

                if not self._set_position(target):
                    return False

                # Wait for movement to complete
                self._wait_for_movement()

                # Hold position
                if hold_time > 0:
                    time.sleep(hold_time)

                return True

            except Exception as e:
                logger.error(f"Failed to open door: {e}")
                return False

            finally:
                self.is_moving = False

    def close_door(self, hold_time: float = 0.5) -> bool:
        """
        Close door by returning servo to closed position.

        Args:
            hold_time: Time to hold position in seconds

        Returns:
            True if successful
        """
        with self.lock:
            if self.is_moving:
                logger.warning("Servo already moving, ignoring command")
                return False

            self.is_moving = True

            try:
                logger.info(f"Closing door: moving to position {self.closed_position}")
                print(f"Closing door: moving to position {self.closed_position}")

                if not self._set_position(self.closed_position):
                    return False

                # Wait for movement to complete
                self._wait_for_movement()

                # Hold position
                if hold_time > 0:
                    time.sleep(hold_time)

                return True

            except Exception as e:
                logger.error(f"Failed to close door: {e}")
                return False

            finally:
                self.is_moving = False

    def get_position(self) -> int:
        """Get current servo position."""
        if not self._connected or not self.servo:
            return self.current_position

        try:
            position, _, _ = self.servo.ReadPos(self.servo_id)
            self.current_position = position
            return position
        except Exception:
            return self.current_position

    def is_door_open(self) -> bool:
        """Check if door is in open position."""
        # Consider open if position is more than 10% of open position
        threshold = self.open_position * 0.1
        return self.get_position() > threshold

    def disable_servo(self) -> bool:
        """Disable servo torque to prevent jitter and save power."""
        logger.info("Disabling servo torque")
        return self._enable_torque(False)

    def enable_servo(self) -> bool:
        """Enable servo torque."""
        logger.info("Enabling servo torque")
        return self._enable_torque(True)

    def test_movement(self) -> bool:
        """Test servo movement for debugging."""
        logger.info("Testing servo movement...")
        print("Testing servo movement...")

        test_positions = [0, 1024, 2048, 1024, 0]  # 0, 90, 180, 90, 0 degrees

        for pos in test_positions:
            print(f"  Moving to position {pos}...")
            self._set_position(pos)
            time.sleep(1.0)

        print("Servo test complete")
        return True

    def cleanup(self):
        """Cleanup resources and close connection."""
        with self.lock:
            logger.info("Cleaning up STServo controller...")

            try:
                # Return to closed position
                self._set_position(self.closed_position)
                self._wait_for_movement(timeout=2.0)

                # Disable torque
                self._enable_torque(False)

                # Close port
                if self.port_handler and self._connected:
                    self.port_handler.closePort()

                self._connected = False
                logger.info("STServo cleanup complete")

            except Exception as e:
                logger.error(f"Error during cleanup: {e}")


def main():
    """Test the STServo controller."""
    print("Testing STServo Controller...")

    try:
        servo = STServoController()

        print("\n1. Testing door open...")
        servo.open_door(hold_time=2.0)

        print("\n2. Testing door close...")
        servo.close_door(hold_time=1.0)

        print("\n3. Testing full range movement...")
        servo.test_movement()

        print(f"\n4. Final position: {servo.get_position()}")
        print(f"5. Door open status: {servo.is_door_open()}")

        # Cleanup
        servo.cleanup()

        print("\nAll tests completed successfully!")

    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    main()
