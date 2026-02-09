#!/usr/bin/env python3
"""
VL53L0X Time-of-Flight Distance Sensor Controller

Controls M5Stack Mini ToF unit (VL53L0CXV0DH, VL53L0X compatible) via I2C.
Measures distance for recycling bin fill level monitoring.

Hardware: VL53L0CX on M5Stack Mini ToF unit
Interface: I2C (address 0x29)
Range: ~30mm to ~1200mm (standard mode)
"""

import os
import time
import threading
import logging
import statistics
from dataclasses import dataclass
from typing import Optional, Dict, Any

try:
    import board
    import busio
    import adafruit_vl53l0x
    VL53L0X_AVAILABLE = True
except ImportError:
    VL53L0X_AVAILABLE = False

logger = logging.getLogger(__name__)

# Sentinel values returned by VL53L0X when no object detected or out of range
OUT_OF_RANGE_VALUES = {8190, 8191}


@dataclass
class ToFSensorConfig:
    """Configuration for VL53L0X ToF sensor."""
    i2c_address: int = 0x29
    timing_budget_us: int = 200_000     # 200ms for best accuracy
    sample_count: int = 7               # Odd number for clean median
    sample_delay_ms: float = 50.0       # Delay between samples in ms
    full_distance_mm: int = 150         # Distance when bin is full
    empty_distance_mm: int = 800        # Distance when bin is empty
    simulation_mode: bool = False
    simulation_distance_mm: int = 500   # Simulated reading for testing


class ToFSensor:
    """
    VL53L0X Time-of-Flight distance sensor controller.

    Reads distance via I2C with median filtering for noise reduction.
    Follows TSV6 hardware controller pattern (dataclass config, env var
    overrides, simulation mode, thread safety, context manager).
    """

    def __init__(self, config: Optional[ToFSensorConfig] = None):
        self.config = config or ToFSensorConfig()
        self._load_from_env()

        self._i2c = None
        self._sensor = None
        self._connected = False
        self._lock = threading.Lock()

        self._last_distance_mm: Optional[int] = None
        self._last_read_time: float = 0

        if not VL53L0X_AVAILABLE and not self.config.simulation_mode:
            logger.warning("adafruit-circuitpython-vl53l0x not available - forcing simulation mode")
            self.config.simulation_mode = True

        logger.info(
            f"ToF sensor initialized (addr=0x{self.config.i2c_address:02x}, "
            f"empty={self.config.empty_distance_mm}mm, full={self.config.full_distance_mm}mm, "
            f"sim={self.config.simulation_mode})"
        )

    def _load_from_env(self) -> None:
        """Load configuration from TSV6_TOF_* environment variables."""
        env_map = {
            'TSV6_TOF_I2C_ADDRESS': ('i2c_address', lambda v: int(v, 0)),
            'TSV6_TOF_TIMING_BUDGET': ('timing_budget_us', int),
            'TSV6_TOF_SAMPLE_COUNT': ('sample_count', int),
            'TSV6_TOF_FULL_DISTANCE': ('full_distance_mm', int),
            'TSV6_TOF_EMPTY_DISTANCE': ('empty_distance_mm', int),
            'TSV6_TOF_SIMULATION': ('simulation_mode', lambda v: v.lower() in ('true', '1', 'yes')),
        }
        for env_key, (attr, converter) in env_map.items():
            val = os.environ.get(env_key)
            if val is not None:
                try:
                    setattr(self.config, attr, converter(val))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid value for {env_key}={val}: {e}")

    def connect(self) -> bool:
        """Initialize I2C bus and VL53L0X sensor."""
        if self.config.simulation_mode:
            logger.info("[SIM] ToF sensor connected (simulation)")
            self._connected = True
            return True

        with self._lock:
            try:
                self._i2c = busio.I2C(board.SCL, board.SDA)
                self._sensor = adafruit_vl53l0x.VL53L0X(self._i2c, address=self.config.i2c_address)
                self._sensor.measurement_timing_budget = self.config.timing_budget_us
                self._connected = True
                logger.info(f"VL53L0X connected at 0x{self.config.i2c_address:02x}")
                return True
            except Exception as e:
                logger.error(f"Failed to connect VL53L0X: {e}")
                self._connected = False
                return False

    def read_distance_mm(self) -> Optional[int]:
        """
        Read distance with median filter for noise reduction.

        Takes config.sample_count readings and returns the median of valid
        samples. Returns None if sensor not connected or all readings invalid.
        """
        if self.config.simulation_mode:
            self._last_distance_mm = self.config.simulation_distance_mm
            self._last_read_time = time.time()
            return self._last_distance_mm

        if not self._connected or self._sensor is None:
            logger.warning("ToF sensor not connected")
            return None

        with self._lock:
            samples = []
            for i in range(self.config.sample_count):
                try:
                    distance = self._sensor.range
                    if distance not in OUT_OF_RANGE_VALUES:
                        samples.append(distance)
                except Exception as e:
                    logger.warning(f"ToF sample {i + 1} failed: {e}")

                if i < self.config.sample_count - 1:
                    time.sleep(self.config.sample_delay_ms / 1000.0)

            if not samples:
                logger.warning("All ToF samples out of range or failed")
                self._last_distance_mm = None
                return None

            median_distance = int(statistics.median(samples))
            self._last_distance_mm = median_distance
            self._last_read_time = time.time()

            logger.debug(
                f"ToF: {len(samples)}/{self.config.sample_count} valid samples, "
                f"median={median_distance}mm"
            )
            return median_distance

    def get_status(self) -> Dict[str, Any]:
        """Get sensor status dict."""
        return {
            'connected': self._connected,
            'last_distance_mm': self._last_distance_mm,
            'last_read_time': self._last_read_time,
            'i2c_address': f"0x{self.config.i2c_address:02x}",
            'empty_distance_mm': self.config.empty_distance_mm,
            'full_distance_mm': self.config.full_distance_mm,
            'simulation_mode': self.config.simulation_mode,
        }

    def cleanup(self) -> None:
        """Release I2C resources."""
        logger.info("Cleaning up ToF sensor...")
        self._connected = False
        if self._i2c:
            try:
                self._i2c.deinit()
            except Exception:
                pass
            self._i2c = None
        self._sensor = None
        logger.info("ToF sensor cleanup complete")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False
