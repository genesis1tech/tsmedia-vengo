"""
Infrared Recycling Verification Sensor

M5Stack U175 IR Emitter + Receiver Unit connected via GPIO.
- Digital output: configurable active-low (default) or active-high
- GPIO pin: BCM 17 (configurable via TSV6_RECYCLE_SENSOR_GPIO)

The sensor monitors for item deposit during the door open cycle:
- Starts AFTER servo reaches fully open position (avoids door motion false positives)
- Stops BEFORE servo begins closing (same reason)
- Reports whether item was detected during that window
"""

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SensorState(Enum):
    """Sensor detection states"""
    IDLE = "idle"
    MONITORING = "monitoring"
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    ERROR = "error"


@dataclass
class RecycleSensorConfig:
    """Configuration for recycling verification sensor"""
    gpio_pin: int = 17  # BCM GPIO pin
    poll_interval: float = 0.05  # 50ms polling interval
    active_low: bool = True  # Sensor outputs LOW when object detected
    simulation_mode: bool = False
    # Debounce: require N consecutive readings to confirm detection
    debounce_count: int = 2


class RecycleSensor:
    """
    Infrared sensor for verifying item deposit into recycling bin.

    Uses M5Stack U175 IR Emitter + Receiver Unit.
    Digital output monitored via GPIO.

    Lifecycle:
        1. start_monitoring() - Called after servo fully opens door
        2. Sensor polls GPIO continuously in background
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

        # Setup GPIO on init
        if not self.config.simulation_mode:
            self._setup_gpio()

        logger.info(
            f"RecycleSensor initialized on GPIO{self.config.gpio_pin} "
            f"(simulation={self.config.simulation_mode})"
        )

    def _load_from_env(self):
        """Load configuration from environment variables"""
        if pin := os.environ.get('TSV6_RECYCLE_SENSOR_GPIO'):
            self.config.gpio_pin = int(pin)
        if interval := os.environ.get('TSV6_RECYCLE_SENSOR_POLL_INTERVAL'):
            self.config.poll_interval = float(interval)
        if sim := os.environ.get('TSV6_RECYCLE_SENSOR_SIMULATION'):
            self.config.simulation_mode = sim.lower() in ('true', '1', 'yes')
        if debounce := os.environ.get('TSV6_RECYCLE_SENSOR_DEBOUNCE'):
            self.config.debounce_count = int(debounce)

    def _setup_gpio(self) -> bool:
        """Configure GPIO pin as input with pull-up"""
        try:
            gpio = self.config.gpio_pin
            result = subprocess.run(
                ['pinctrl', 'set', str(gpio), 'ip', 'pu'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info(f"GPIO{gpio} configured as input with pull-up")
                return True
            else:
                logger.error(f"GPIO setup failed: {result.stderr}")
                return False
        except FileNotFoundError:
            logger.warning("pinctrl not found - may not be on Raspberry Pi")
            return False
        except Exception as e:
            logger.error(f"Failed to setup GPIO: {e}")
            return False

    def _read_gpio(self) -> bool:
        """
        Read GPIO pin state.

        Returns:
            True if object detected, False otherwise
        """
        if self.config.simulation_mode:
            return False

        try:
            gpio = self.config.gpio_pin
            result = subprocess.run(
                ['pinctrl', 'get', str(gpio)],
                capture_output=True,
                text=True,
                timeout=1
            )

            output = result.stdout.lower()
            is_high = 'hi' in output or 'level=1' in output

            if self.config.active_low:
                return not is_high  # Object detected when LOW
            else:
                return is_high  # Object detected when HIGH

        except Exception as e:
            logger.error(f"Failed to read GPIO: {e}")
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
            if self._read_gpio():
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
            else:
                consecutive_detections = 0

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
        logger.info("RecycleSensor cleaned up")

    def __repr__(self) -> str:
        return (
            f"RecycleSensor(gpio={self.config.gpio_pin}, "
            f"state={self._state.value}, "
            f"detected={self._item_detected})"
        )
