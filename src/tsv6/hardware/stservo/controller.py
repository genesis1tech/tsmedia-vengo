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
import glob
from typing import Optional, Tuple, Callable
from pathlib import Path

# Add vendor directory to Python path for scservo_sdk imports
import sys
_vendor_path = Path(__file__).parent / 'vendor'
if str(_vendor_path) not in sys.path:
    sys.path.insert(0, str(_vendor_path))

try:
    from scservo_sdk import PortHandler, sms_sts, SMS_STS_TORQUE_ENABLE
    from scservo_sdk.sms_sts import SMS_STS_PRESENT_CURRENT_L, SMS_STS_PRESENT_LOAD_L
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
        baudrate: int = 1000000,  # ST3020 default is 1Mbps
        servo_id: int = 1,
        open_position: int = 2868,      # Open position (calibrated)
        closed_position: int = 4070,    # Closed position (calibrated)
        moving_speed: int = 0,          # 0 = maximum speed
        acceleration: int = 50,         # Acceleration value
        timeout: float = 1.0,
        on_obstruction_callback: Optional[Callable[[int, int], None]] = None,
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
            on_obstruction_callback: Callback function(retry_count, servo_id) called on obstruction
        """
        # Load from environment variables if not specified.  Production defaults
        # to real hardware; simulation must be opted into explicitly so a
        # missing USB adapter cannot be mistaken for a successful door move.
        env_port = os.environ.get('TSV6_SERVO_PORT')
        self._explicit_port = port or env_port
        self.simulation_mode = os.environ.get(
            'TSV6_SERVO_SIMULATION', 'false'
        ).lower() in ('true', '1', 'yes')
        self.port = self._explicit_port or self._auto_detect_port()
        self.baudrate = int(os.environ.get('TSV6_SERVO_BAUD', baudrate))
        self.servo_id = int(os.environ.get('TSV6_SERVO_ID', servo_id))
        calibration = self._read_calibration_file()
        self.open_position = int(os.environ.get(
            'TSV6_SERVO_OPEN_POS',
            calibration.get('TSV6_SERVO_OPEN_POS', open_position),
        ))
        self.closed_position = int(os.environ.get(
            'TSV6_SERVO_CLOSED_POS',
            calibration.get('TSV6_SERVO_CLOSED_POS', closed_position),
        ))
        self.moving_speed = int(os.environ.get('TSV6_SERVO_SPEED', moving_speed))
        self.acceleration = acceleration
        self.timeout = timeout

        self.current_position = 0
        self.is_moving = False
        self.lock = threading.Lock()

        self.port_handler: Optional[PortHandler] = None
        self.servo: Optional[sms_sts] = None
        self._connected = False
        self.on_obstruction_callback = on_obstruction_callback

        if not STSERVO_AVAILABLE:
            logger.warning("STServo SDK not available - running in simulation mode")
            print("STServo SDK not available - running in simulation mode")
            self.simulation_mode = True
            return

        self._connect()

    @staticmethod
    def _calibration_file_path() -> Path:
        """Return the persistent servo calibration file path."""
        return Path(
            os.environ.get(
                "TSV6_SERVO_CALIBRATION_FILE",
                str(Path.home() / ".config" / "tsv6" / "servo-calibration.env"),
            )
        )

    @classmethod
    def _read_calibration_file(cls) -> dict[str, int]:
        """Read persisted servo positions from a simple KEY=VALUE env file."""
        path = cls._calibration_file_path()
        if not path.exists():
            return {}

        values: dict[str, int] = {}
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw_value = line.split("=", 1)
                key = key.strip()
                raw_value = raw_value.strip().strip('"').strip("'")
                if key in ("TSV6_SERVO_OPEN_POS", "TSV6_SERVO_CLOSED_POS"):
                    values[key] = cls._validate_position(raw_value)
        except Exception as exc:
            logger.warning("Failed to read servo calibration file %s: %s", path, exc)
        return values

    @staticmethod
    def _validate_position(value) -> int:
        """Validate and clamp a raw servo position to the supported range."""
        position = int(value)
        return max(0, min(4095, position))

    def get_calibration(self) -> dict:
        """Return current calibration and live servo status."""
        return {
            "open_position": int(self.open_position),
            "closed_position": int(self.closed_position),
            "current_position": int(self.get_position()),
            "connected": bool(self.is_connected),
            "simulation": bool(self.simulation_mode),
            "port": self.port,
            "servo_id": self.servo_id,
            "calibration_file": str(self._calibration_file_path()),
        }

    def set_calibration(
        self,
        open_position: Optional[int] = None,
        closed_position: Optional[int] = None,
        persist: bool = True,
    ) -> dict:
        """Update open/closed positions and optionally persist them."""
        if open_position is not None:
            self.open_position = self._validate_position(open_position)
        if closed_position is not None:
            self.closed_position = self._validate_position(closed_position)
        if persist:
            self._write_calibration_file()
        return self.get_calibration()

    def _write_calibration_file(self) -> None:
        """Persist the current calibration for future service restarts."""
        path = self._calibration_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "# TSV6 servo calibration. Environment variables still override these values.",
                    f"TSV6_SERVO_OPEN_POS={int(self.open_position)}",
                    f"TSV6_SERVO_CLOSED_POS={int(self.closed_position)}",
                    "",
                ]
            )
        )

    def _auto_detect_port(self) -> str:
        """Auto-detect the serial port for the USB adapter."""
        ports = self._auto_detect_ports()
        if ports:
            port = ports[0]
            if 'ttyUSB' in port:
                logger.warning(f"Using {port} for servo - may conflict with LTE modem. "
                               "Consider setting TSV6_SERVO_PORT explicitly.")
            else:
                logger.info(f"Auto-detected servo port: {port}")
            return port

        logger.warning("No serial port auto-detected, using /dev/ttyACM0")
        return '/dev/ttyACM0'

    def _auto_detect_ports(self) -> list[str]:
        """Return servo candidates ordered by stable device identity first."""
        # This is the most reliable method as it identifies the specific device
        # regardless of which physical USB port the adapter is plugged into.
        candidates = ['/dev/tsv6-servo']
        by_id_patterns = [
            '/dev/serial/by-id/usb-1a86_USB_Single_Serial*',  # QinHeng CH340/CH341
            '/dev/serial/by-id/*CH340*',
            '/dev/serial/by-id/*CH341*',
        ]
        for pattern in by_id_patterns:
            candidates.extend(sorted(glob.glob(pattern)))

        # Common port paths - prioritize ttyACM (typical for CH340 on newer kernels)
        # over ttyUSB (which is often used by LTE modems)
        candidates.extend([
            '/dev/ttyACM0',     # CH340 often appears as ACM on newer kernels
            '/dev/ttyACM1',
            '/dev/ttyUSB0',     # Fallback - may conflict with LTE modem
            '/dev/ttyUSB1',
        ])

        ports: list[str] = []
        seen: set[str] = set()
        for port in candidates:
            if port in seen or not os.path.exists(port):
                continue
            ports.append(port)
            seen.add(port)
        return ports

    def _connect(self) -> bool:
        """Connect to the servo via serial port."""
        if not STSERVO_AVAILABLE:
            return False

        try:
            ports = [self._explicit_port] if self._explicit_port else self._auto_detect_ports()
            if not ports:
                ports = ['/dev/ttyACM0']

            for port in ports:
                self.port = port
                if self._connect_on_current_port():
                    return True

            logger.error("Failed to connect to STServo on candidate ports: %s", ports)
            return False

        except Exception as e:
            logger.error(f"Failed to connect to STServo: {e}")
            self._connected = False
            self.servo = None
            return False

    def _connect_on_current_port(self) -> bool:
        """Try one candidate port and verify the configured servo responds."""
        logger.info(f"Connecting to STServo on {self.port} at {self.baudrate} baud...")
        self._connected = False

        self.port_handler = PortHandler(self.port)
        self.port_handler.baudrate = self.baudrate

        if not self.port_handler.openPort():
            logger.error(f"Failed to open port {self.port}")
            self.port_handler = None
            return False

        self.servo = sms_sts(self.port_handler)

        ping_result = self.servo.ping(self.servo_id)
        comm_result = ping_result[1] if isinstance(ping_result, tuple) and len(ping_result) > 1 else 0
        if comm_result != 0:
            logger.error(
                f"STServo adapter opened on {self.port}, but servo ID {self.servo_id} "
                f"did not respond to ping (comm_result={comm_result}). "
                "Check servo power, TTL bus wiring, and servo ID."
            )
            try:
                self.port_handler.closePort()
            except Exception:
                pass
            self.servo = None
            self.port_handler = None
            return False

        self._connected = True

        # Enable torque
        self._enable_torque(True)

        # Move servo to closed position (0) on startup/wake-up
        logger.info("Initializing servo to closed position (0)...")
        self._set_position(self.closed_position)
        self._wait_for_movement(timeout=2.0)

        logger.info(f"STServo connected on {self.port} (ID: {self.servo_id})")
        print(f"STServo connected on {self.port} (ID: {self.servo_id})")

        return True

    @property
    def is_connected(self) -> bool:
        """True when the controller has an active hardware connection."""
        return self._connected and self.servo is not None

    def _ensure_connected(self) -> bool:
        """Reconnect after adapter unplug/replug before issuing commands."""
        if self.is_connected:
            return True
        if self.simulation_mode:
            return False

        logger.warning("STServo not connected; attempting adapter re-detection/reconnect")
        return self._connect()

    def _enable_torque(self, enable: bool) -> bool:
        """Enable or disable servo torque."""
        if not self._connected or not self.servo:
            if enable and not self.simulation_mode and not self._ensure_connected():
                logger.error("Cannot enable servo torque: servo adapter is not connected")
                return False
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
            if not self.simulation_mode and not self._ensure_connected():
                logger.error(
                    f"Cannot move servo to position {position}: servo adapter is not connected"
                )
                return False

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

    def _verify_reached(
        self,
        target: int,
        op: str,
        tolerance: int = 50,
    ) -> bool:
        """Read back position and confirm the servo physically reached target.

        Guards against the silent-failure mode where _set_position returns
        True and _wait_for_movement returns True (because nothing is moving)
        but the servo never actually moved — typically because torque was
        disabled. On mismatch, re-enables torque, retries the position write
        once, and reports failure if it still doesn't reach.

        Returns True if the servo is within ``tolerance`` of ``target``.
        """
        if not self._connected or not self.servo:
            if self.simulation_mode:
                return True  # simulation mode — nothing to verify
            logger.error(f"{op}: cannot verify target {target}; servo adapter is not connected")
            return False

        actual = self.get_position()
        if abs(actual - target) <= tolerance:
            return True

        logger.error(
            f"{op}: position-write completed but servo at {actual}, target {target} "
            f"(delta {abs(actual - target)} > {tolerance}). "
            f"Likely torque was disabled or movement was blocked. Retrying once."
        )
        print(
            f"{op}: servo did not reach target ({actual} vs {target}); "
            f"re-enabling torque and retrying"
        )
        self._enable_torque(True)
        if not self._set_position(target):
            return False
        self._wait_for_movement()

        actual = self.get_position()
        if abs(actual - target) <= tolerance:
            logger.info(f"{op}: retry succeeded — servo now at {actual}")
            return True

        logger.error(
            f"{op}: retry failed — servo still at {actual}, target {target}. "
            f"Check servo power, USB serial connection, and obstruction state."
        )
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

                # Torque may have been disabled out from under us by a previous
                # obstruction-retry exhaustion, by the obstruction-handler
                # service on its own exit, or by an explicit disable_servo()
                # call. A WritePosEx to a torque-off servo silently no-ops, so
                # we always re-arm torque before issuing motion commands.
                self._enable_torque(True)

                logger.info(f"Opening door: moving to position {target}")
                print(f"Opening door: moving to position {target}")

                if not self._set_position(target):
                    return False

                if not self._wait_for_movement():
                    logger.warning(
                        f"open_door: _wait_for_movement timed out "
                        f"(target={target}, current={self.get_position()})"
                    )

                if not self._verify_reached(target, op="open_door"):
                    return False

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
                # See open_door: defensively re-arm torque before motion.
                self._enable_torque(True)

                logger.info(f"Closing door: moving to position {self.closed_position}")
                print(f"Closing door: moving to position {self.closed_position}")

                if not self._set_position(self.closed_position):
                    return False

                if not self._wait_for_movement():
                    logger.warning(
                        f"close_door: _wait_for_movement timed out "
                        f"(target={self.closed_position}, current={self.get_position()})"
                    )

                if not self._verify_reached(self.closed_position, op="close_door"):
                    return False

                if hold_time > 0:
                    time.sleep(hold_time)

                return True

            except Exception as e:
                logger.error(f"Failed to close door: {e}")
                return False

            finally:
                self.is_moving = False

    def _monitor_close_movement(self, timeout: float = 3.0) -> bool:
        """
        Monitor servo during close movement for obstructions.

        Args:
            timeout: Maximum time to wait for movement completion

        Returns:
            True if obstruction detected, False if movement completed normally
        """
        if not self._connected or not self.servo:
            if not self.simulation_mode:
                logger.error("Cannot monitor close movement: servo adapter is not connected")
                return True
            # Simulation mode - no obstruction
            time.sleep(0.5)
            return False

        start_time = time.time()
        baseline_current = self.read_current()
        last_position = self.get_position()
        stall_count = 0

        # Current spike threshold (200% above baseline, minimum 500)
        # Normal movement draws ~16-50mA, obstructions draw significantly more
        current_threshold = max(baseline_current * 3.0, 500)

        logger.debug(f"Monitoring close: baseline_current={baseline_current}, threshold={current_threshold}")

        while time.time() - start_time < timeout:
            try:
                current = self.read_current()
                position = self.get_position()
                moving, _, _ = self.servo.ReadMoving(self.servo_id)

                # Check 1: Current spike (obstruction causing motor strain)
                if current > current_threshold:
                    logger.warning(f"Obstruction detected: current spike {current} > {current_threshold}")
                    return True

                # Check 2: Movement completed - verify position
                if not moving:
                    # Check if we reached the target (within tolerance)
                    if abs(position - self.closed_position) < 50:
                        logger.debug(f"Close completed: position={position}, target={self.closed_position}")
                        return False  # Success - no obstruction
                    else:
                        logger.warning(f"Obstruction detected: stopped at {position}, target={self.closed_position}")
                        return True  # Stopped but not at target = blocked

                # Check 3: Position stall (moving flag set but no position change)
                if abs(position - last_position) < 10:
                    stall_count += 1
                    if stall_count > 10:  # ~500ms of no movement
                        logger.warning(f"Obstruction detected: position stalled at {position}")
                        return True
                else:
                    stall_count = 0

                last_position = position
                time.sleep(0.05)  # Poll every 50ms

            except Exception as e:
                logger.error(f"Error during movement monitoring: {e}")
                break

        # Timeout - check final position
        final_position = self.get_position()
        if abs(final_position - self.closed_position) > 50:
            logger.warning(f"Obstruction detected: timeout at position {final_position}")
            return True

        return False

    def close_door_with_safety(
        self,
        max_retries: int = 3,
        retry_delay: float = 5.0,
        hold_time: float = 0.5
    ) -> Tuple[bool, str]:
        """
        Close door with obstruction detection and automatic retry.

        Safety feature: If obstruction detected, opens door immediately,
        waits, then retries. After max_retries, stays open and reports.

        Args:
            max_retries: Maximum number of close attempts (default: 3)
            retry_delay: Seconds to wait between retries (default: 5.0)
            hold_time: Seconds to hold closed position (default: 0.5)

        Returns:
            Tuple of (success, status) where status is:
            - "closed": Door successfully closed
            - "obstructed": Obstruction detected, door left open
            - "error": Communication or other error
        """
        with self.lock:
            if self.is_moving:
                logger.warning("Servo already moving, ignoring command")
                return (False, "error")

            self.is_moving = True

            try:
                # Re-arm torque before close attempts.  The previous obstruction-
                # exhaustion path leaves torque disabled (so the user can free
                # the obstruction); without this re-enable, a follow-up scan
                # would silently fail to move the servo at all.
                self._enable_torque(True)

                for attempt in range(max_retries):
                    logger.info(f"Close attempt {attempt + 1}/{max_retries}")
                    print(f"Close attempt {attempt + 1}/{max_retries}")

                    # Start close movement
                    if not self._set_position(self.closed_position):
                        return (False, "error")

                    # Monitor for obstruction during movement
                    obstruction_detected = self._monitor_close_movement()

                    if not obstruction_detected:
                        # Success - hold position and return
                        if hold_time > 0:
                            time.sleep(hold_time)
                        logger.info("Door closed successfully")
                        print("Door closed successfully")
                        return (True, "closed")

                    # Obstruction detected - open immediately for safety
                    logger.warning(f"Obstruction on attempt {attempt + 1}, opening door")
                    print(f"Obstruction detected on attempt {attempt + 1}, opening door")

                    self._set_position(self.open_position)
                    self._wait_for_movement()

                    # Notify via callback if registered
                    if self.on_obstruction_callback:
                        try:
                            self.on_obstruction_callback(attempt + 1, self.servo_id)
                        except Exception as e:
                            logger.error(f"Obstruction callback error: {e}")

                    # Wait before retry (except on last attempt)
                    if attempt < max_retries - 1:
                        logger.info(f"Waiting {retry_delay}s before retry...")
                        print(f"Waiting {retry_delay}s before retry...")
                        time.sleep(retry_delay)

                # All retries exhausted - stay open and disable torque
                # This allows the user to freely remove the obstructing item
                logger.error(f"Obstruction persists after {max_retries} attempts, door left open")
                print(f"Obstruction persists after {max_retries} attempts, door left open")

                logger.info("Disabling servo torque to allow item removal")
                print("Disabling servo torque - item can be removed freely")
                self._enable_torque(False)

                return (False, "obstructed")

            except Exception as e:
                logger.error(f"Error during safe close: {e}")
                return (False, "error")

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

    def read_current(self) -> int:
        """
        Read servo current draw.

        Returns:
            Current in mA (approximate), 0 if not connected
        """
        if not self._connected or not self.servo:
            return 0

        try:
            current, _, _ = self.servo.read2ByteTxRx(self.servo_id, SMS_STS_PRESENT_CURRENT_L)
            return current
        except Exception as e:
            logger.debug(f"Failed to read current: {e}")
            return 0

    def read_load(self) -> int:
        """
        Read servo load/torque.

        Returns:
            Load value (0-1000 scale), 0 if not connected
        """
        if not self._connected or not self.servo:
            return 0

        try:
            load, _, _ = self.servo.read2ByteTxRx(self.servo_id, SMS_STS_PRESENT_LOAD_L)
            return load
        except Exception as e:
            logger.debug(f"Failed to read load: {e}")
            return 0

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
            if not self._set_position(pos):
                return False
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
