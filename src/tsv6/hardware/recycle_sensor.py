"""
ToF-Based Recycling Verification Sensor

M5Stack ToF4M unit (U172, VL53L1X chip) connected via I2C bus 3.
- I2C bus 2: GPIO 4 (SDA), GPIO 5 (SCL)
- I2C address: 0x29 (default)
- Detection: distance below configurable threshold = item present

The sensor monitors for item deposit during the door open cycle:
- Starts AFTER servo reaches fully open position (avoids door motion false positives)
- Stops BEFORE servo begins closing (same reason)
- Reports whether item was detected during that window
"""

import logging
import os
import statistics
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    import adafruit_vl53l1x
    from adafruit_extended_bus import ExtendedI2C
    VL53L1X_AVAILABLE = True
except ImportError:
    VL53L1X_AVAILABLE = False


class SensorState(Enum):
    """Sensor detection states"""
    IDLE = "idle"
    MONITORING = "monitoring"
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    ERROR = "error"


@dataclass
class RecycleSensorConfig:
    """Configuration for ToF-based recycling verification sensor"""
    i2c_bus: int = 2                      # I2C bus number (/dev/i2c-2)
    i2c_address: int = 0x29               # VL53L1X default address
    poll_interval: float = 0.05           # 50ms polling interval
    detection_threshold_mm: int = 110     # Item detected if distance < this
    distance_mode: int = 1                # 1=short (~136cm), 2=long (~360cm)
    timing_budget_ms: int = 50            # Ranging duration in ms
    simulation_mode: bool = False
    debounce_count: int = 2               # Consecutive readings for confirmation
    baseline_sample_count: int = 5        # Samples for auto-calibration


class RecycleSensor:
    """
    VL53L1X ToF sensor for verifying item deposit into recycling bin.

    Uses M5Stack ToF4M unit (U172) on I2C bus 3.
    Measures distance and detects items below a configurable threshold.

    Lifecycle:
        1. start_monitoring() - Called after servo fully opens door
        2. Sensor polls distance continuously in background
        3. detection_event is set when item detected (allows wait with timeout)
        4. stop_monitoring() - Called before servo begins closing
        5. Check was_item_detected() for result
    """

    def __init__(
        self,
        config: Optional[RecycleSensorConfig] = None,
        on_detection: Optional[Callable[[], None]] = None
    ):
        self.config = config or RecycleSensorConfig()
        self._load_from_env()

        self.on_detection = on_detection

        self._state = SensorState.IDLE
        self._lock = threading.Lock()
        self._stop_monitoring_flag = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._item_detected = False
        self._detection_time: Optional[float] = None
        self._monitoring_start_time: Optional[float] = None

        # Public event for callers to wait on detection with timeout
        self.detection_event = threading.Event()

        # I2C/sensor handles
        self._i2c = None
        self._sensor = None
        self._connected = False

        # Baseline distance (auto-calibrated on connect)
        self._baseline_distance_mm: Optional[int] = None

        # Force simulation if library not available
        if not VL53L1X_AVAILABLE and not self.config.simulation_mode:
            logger.warning(
                "adafruit-circuitpython-vl53l1x not available - "
                "forcing simulation mode"
            )
            self.config.simulation_mode = True

        # Auto-connect on init
        if not self.config.simulation_mode:
            self._connect_sensor()

        logger.info(
            f"RecycleSensor initialized (i2c_bus={self.config.i2c_bus}, "
            f"threshold={self.config.detection_threshold_mm}mm, "
            f"simulation={self.config.simulation_mode})"
        )

    def _load_from_env(self):
        """Load configuration from environment variables"""
        env_map = {
            'TSV6_RECYCLE_SENSOR_I2C_BUS': ('i2c_bus', int),
            'TSV6_RECYCLE_SENSOR_I2C_ADDRESS': ('i2c_address', lambda v: int(v, 0)),
            'TSV6_RECYCLE_SENSOR_POLL_INTERVAL': ('poll_interval', float),
            'TSV6_RECYCLE_SENSOR_THRESHOLD_MM': ('detection_threshold_mm', int),
            'TSV6_RECYCLE_SENSOR_DISTANCE_MODE': ('distance_mode', int),
            'TSV6_RECYCLE_SENSOR_TIMING_BUDGET': ('timing_budget_ms', int),
            'TSV6_RECYCLE_SENSOR_SIMULATION': (
                'simulation_mode',
                lambda v: v.lower() in ('true', '1', 'yes')
            ),
            'TSV6_RECYCLE_SENSOR_DEBOUNCE': ('debounce_count', int),
        }
        for env_key, (attr, converter) in env_map.items():
            val = os.environ.get(env_key)
            if val is not None:
                try:
                    setattr(self.config, attr, converter(val))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid value for {env_key}={val}: {e}")

    def _connect_sensor(self) -> bool:
        """Initialize I2C bus and VL53L1X sensor."""
        if self.config.simulation_mode:
            self._connected = True
            return True

        try:
            self._i2c = ExtendedI2C(self.config.i2c_bus)
            self._sensor = adafruit_vl53l1x.VL53L1X(
                self._i2c, address=self.config.i2c_address
            )
            self._sensor.distance_mode = self.config.distance_mode
            self._sensor.timing_budget = self.config.timing_budget_ms
            self._connected = True
            logger.info(
                f"VL53L1X connected on i2c-{self.config.i2c_bus} "
                f"at 0x{self.config.i2c_address:02x}"
            )

            # Auto-calibrate baseline
            self._calibrate_baseline()
            return True
        except Exception as e:
            logger.error(f"Failed to connect VL53L1X: {e}")
            self._connected = False
            return False

    def _calibrate_baseline(self):
        """Read empty chute distance to establish baseline for diagnostics."""
        try:
            samples = []
            self._sensor.start_ranging()
            for _ in range(self.config.baseline_sample_count):
                timeout_start = time.monotonic()
                while not self._sensor.data_ready:
                    if time.monotonic() - timeout_start > 1.0:
                        break
                    time.sleep(0.01)
                if self._sensor.data_ready:
                    dist_cm = self._sensor.distance
                    self._sensor.clear_interrupt()
                    if dist_cm is not None and dist_cm > 0:
                        samples.append(int(dist_cm * 10))  # cm → mm
                time.sleep(0.05)
            self._sensor.stop_ranging()

            if samples:
                self._baseline_distance_mm = int(statistics.median(samples))
                logger.info(
                    f"Baseline calibrated: {self._baseline_distance_mm}mm "
                    f"({len(samples)} samples)"
                )
            else:
                logger.warning("Baseline calibration failed - using threshold only")
        except Exception as e:
            logger.warning(f"Baseline calibration error: {e}")

    def _read_distance(self) -> Optional[bool]:
        """
        Read distance from VL53L1X and determine if object is present.

        Returns:
            True if object detected (distance < threshold),
            False if no object (distance >= threshold),
            None if no data ready yet (sensor still ranging).
        """
        if self.config.simulation_mode:
            return False

        if not self._connected or self._sensor is None:
            return False

        try:
            if self._sensor.data_ready:
                distance_cm = self._sensor.distance
                self._sensor.clear_interrupt()
                if distance_cm is not None and distance_cm > 0:
                    distance_mm = int(distance_cm * 10)
                    detected = distance_mm < self.config.detection_threshold_mm
                    logger.debug(f"ToF: {distance_mm}mm {'< ' if detected else '>='}{self.config.detection_threshold_mm}mm")
                    return detected
                else:
                    logger.debug(f"ToF invalid reading: {distance_cm}")
                    return None
            return None
        except Exception as e:
            logger.error(f"Failed to read distance: {e}")
            return False

    @property
    def state(self) -> SensorState:
        """Current sensor state"""
        with self._lock:
            return self._state

    def _set_state(self, new_state: SensorState):
        """Set sensor state"""
        with self._lock:
            old_state = self._state
            self._state = new_state
            if old_state != new_state:
                logger.debug(f"Sensor state: {old_state.value} -> {new_state.value}")

    def start_monitoring(self) -> bool:
        """
        Start monitoring for object detection.

        Called after servo door is fully open.
        Monitoring continues until stop_monitoring() is called.

        Returns:
            True if monitoring started successfully
        """
        if self._state == SensorState.MONITORING:
            logger.warning("Already monitoring - resetting")
            self.stop_monitoring()

        # Reset detection state
        self._item_detected = False
        self._detection_time = None
        self._monitoring_start_time = time.monotonic()
        self.detection_event.clear()

        self._stop_monitoring_flag.clear()
        self._set_state(SensorState.MONITORING)

        # Start VL53L1X continuous ranging
        if self._connected and self._sensor and not self.config.simulation_mode:
            try:
                self._sensor.start_ranging()
            except Exception as e:
                logger.error(f"Failed to start ranging: {e}")

        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            name="RecycleSensor-Monitor",
            daemon=True
        )
        self._monitor_thread.start()

        logger.info("Started monitoring for item detection")
        return True

    def _monitoring_loop(self):
        """Background thread for continuous sensor monitoring"""
        consecutive_detections = 0

        while not self._stop_monitoring_flag.is_set():
            result = self._read_distance()

            if result is True:
                consecutive_detections += 1

                if consecutive_detections >= self.config.debounce_count:
                    if not self._item_detected:
                        elapsed = time.monotonic() - self._monitoring_start_time
                        self._item_detected = True
                        self._detection_time = elapsed
                        self._set_state(SensorState.DETECTED)
                        self.detection_event.set()
                        logger.info(f"Item detected at {elapsed:.2f}s")

                        if self.on_detection:
                            try:
                                self.on_detection()
                            except Exception as e:
                                logger.error(f"Detection callback error: {e}")
            elif result is False:
                # Actual above-threshold reading — reset counter
                consecutive_detections = 0
            # result is None — no data ready, keep counter as-is

            time.sleep(self.config.poll_interval)

        logger.debug("Monitoring loop exited")

    def stop_monitoring(self) -> bool:
        """
        Stop monitoring and finalize detection result.

        Called before servo begins closing door.

        Returns:
            True if item was detected during monitoring window
        """
        self._stop_monitoring_flag.set()

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)

        # Stop VL53L1X ranging
        if self._connected and self._sensor and not self.config.simulation_mode:
            try:
                self._sensor.stop_ranging()
            except Exception as e:
                logger.error(f"Failed to stop ranging: {e}")

        if self._item_detected:
            self._set_state(SensorState.DETECTED)
            logger.info(
                f"Monitoring stopped - item WAS detected "
                f"(at {self._detection_time:.2f}s)"
            )
        else:
            self._set_state(SensorState.NOT_DETECTED)
            if self._monitoring_start_time:
                duration = time.monotonic() - self._monitoring_start_time
                logger.warning(
                    f"Monitoring stopped - item NOT detected "
                    f"(monitored for {duration:.2f}s)"
                )

        return self._item_detected

    def was_item_detected(self) -> bool:
        """Check if item was detected during the last monitoring session"""
        return self._item_detected

    def get_detection_time(self) -> Optional[float]:
        """Seconds from monitoring start to detection, or None if not detected"""
        return self._detection_time

    def reset(self):
        """Reset sensor to idle state for next transaction"""
        self.stop_monitoring()
        self._item_detected = False
        self._detection_time = None
        self._monitoring_start_time = None
        self.detection_event.clear()
        self._set_state(SensorState.IDLE)

    def is_monitoring(self) -> bool:
        """Check if sensor is currently monitoring"""
        return self._state == SensorState.MONITORING

    def cleanup(self):
        """Cleanup resources"""
        self.stop_monitoring()
        self._connected = False
        if self._i2c:
            try:
                self._i2c.deinit()
            except Exception:
                pass
            self._i2c = None
        self._sensor = None
        logger.info("RecycleSensor cleaned up")

    def __repr__(self) -> str:
        return (
            f"RecycleSensor(i2c_bus={self.config.i2c_bus}, "
            f"threshold={self.config.detection_threshold_mm}mm, "
            f"state={self._state.value}, "
            f"detected={self._item_detected})"
        )
