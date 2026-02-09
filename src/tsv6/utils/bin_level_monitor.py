#!/usr/bin/env python3
"""
Bin Fill Level Monitor

Periodically reads the VL53L0X ToF sensor and publishes bin fill level
to AWS IoT. Follows the same monitoring loop pattern as LTEMonitor and
NetworkMonitor.

Fill levels (800mm empty, 150mm full):
  empty (0-12%), quarter (13-37%), half (38-62%),
  three_quarter (63-87%), full (88-100%)
"""

import threading
import time
import logging
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..hardware.tof_sensor import ToFSensor
    from .error_recovery import ErrorRecoverySystem

logger = logging.getLogger(__name__)

FILL_LEVEL_THRESHOLDS = [
    (88, "full"),
    (63, "three_quarter"),
    (38, "half"),
    (13, "quarter"),
    (0, "empty"),
]


@dataclass
class BinLevelMonitorConfig:
    """Configuration for bin level monitoring."""
    check_interval_secs: float = 1800.0   # 30 minutes
    startup_delay_secs: float = 30.0      # Let system stabilize first
    full_distance_mm: int = 150           # Items within 150mm = full
    empty_distance_mm: int = 800          # Bin depth from sensor to bottom
    max_consecutive_failures: int = 3


class BinLevelMonitor:
    """
    Bin fill level monitoring system.

    Background daemon thread reads the ToF sensor at a configurable interval
    (default 30 minutes), calculates fill percentage, classifies into a named
    level, and fires a callback with the result.
    """

    def __init__(
        self,
        tof_sensor: 'ToFSensor',
        config: Optional[BinLevelMonitorConfig] = None,
        on_level_update: Optional[Callable[[Dict[str, Any]], None]] = None,
        error_recovery_system: Optional['ErrorRecoverySystem'] = None,
    ) -> None:
        self.sensor = tof_sensor
        self.cfg = config or BinLevelMonitorConfig()
        self.on_level_update = on_level_update
        self.error_recovery = error_recovery_system

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._consecutive_failures = 0

        self._latest_fill_data: Optional[Dict[str, Any]] = None
        self._data_lock = threading.Lock()

        logger.info(
            f"BinLevelMonitor initialized "
            f"(interval={self.cfg.check_interval_secs}s, "
            f"empty={self.cfg.empty_distance_mm}mm, full={self.cfg.full_distance_mm}mm)"
        )

    def start(self) -> None:
        """Start monitoring in background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="BinLevelMonitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("Bin level monitoring started")

    def stop(self) -> None:
        """Stop monitoring."""
        logger.info("Stopping bin level monitor...")
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Bin level monitor stopped")

    @staticmethod
    def calculate_fill_percentage(
        measured_mm: int,
        empty_distance_mm: int,
        full_distance_mm: int,
    ) -> float:
        """Calculate fill percentage from distance measurement.

        Args:
            measured_mm: Distance reading from sensor in mm.
            empty_distance_mm: Distance when bin is empty (sensor to bottom).
            full_distance_mm: Distance when bin is full (items near sensor).

        Returns:
            Fill percentage clamped to 0.0-100.0.
        """
        if empty_distance_mm <= full_distance_mm:
            return 0.0
        raw = (empty_distance_mm - measured_mm) / (empty_distance_mm - full_distance_mm) * 100.0
        return max(0.0, min(100.0, raw))

    @staticmethod
    def fill_percentage_to_level(percentage: float) -> str:
        """Convert percentage to named fill level."""
        for threshold, level in FILL_LEVEL_THRESHOLDS:
            if percentage >= threshold:
                return level
        return "empty"

    def get_latest_fill_data(self) -> Optional[Dict[str, Any]]:
        """Get the most recent fill level data (thread-safe)."""
        with self._data_lock:
            return self._latest_fill_data.copy() if self._latest_fill_data else None

    def _run_loop(self) -> None:
        """Main monitoring loop."""
        logger.info(f"Bin level monitor loop starting (startup delay: {self.cfg.startup_delay_secs}s)")

        # Wait for system to stabilize before first reading
        if self._stop.wait(self.cfg.startup_delay_secs):
            return

        # Take initial reading immediately
        self._take_reading()

        while not self._stop.is_set():
            if self._stop.wait(self.cfg.check_interval_secs):
                break
            self._take_reading()

        logger.info("Bin level monitor loop exited")

    def _take_reading(self) -> None:
        """Take a sensor reading and process fill level."""
        try:
            distance_mm = self.sensor.read_distance_mm()

            if distance_mm is None:
                self._consecutive_failures += 1
                logger.warning(
                    f"ToF sensor read failed "
                    f"(consecutive failures: {self._consecutive_failures})"
                )
                if (self._consecutive_failures >= self.cfg.max_consecutive_failures
                        and self.error_recovery):
                    self.error_recovery.report_error(
                        "tof_sensor",
                        "read_failure",
                        f"ToF sensor failed {self._consecutive_failures} consecutive reads",
                        severity="medium",
                    )
                return

            self._consecutive_failures = 0

            fill_pct = self.calculate_fill_percentage(
                distance_mm, self.cfg.empty_distance_mm, self.cfg.full_distance_mm
            )
            fill_level = self.fill_percentage_to_level(fill_pct)

            fill_data = {
                "distance_mm": distance_mm,
                "fill_percentage": round(fill_pct, 1),
                "fill_level": fill_level,
                "empty_distance_mm": self.cfg.empty_distance_mm,
                "full_distance_mm": self.cfg.full_distance_mm,
                "timestamp": time.time(),
            }

            with self._data_lock:
                self._latest_fill_data = fill_data

            logger.info(
                f"Bin level: {fill_level} ({fill_pct:.1f}%) - distance={distance_mm}mm"
            )

            if self.on_level_update:
                try:
                    self.on_level_update(fill_data)
                except Exception as e:
                    logger.error(f"Level update callback error: {e}")

            if self.error_recovery:
                self.error_recovery.report_success("tof_sensor")

        except Exception as e:
            logger.error(f"Bin level monitor error: {e}", exc_info=True)
            self._consecutive_failures += 1
            if self.error_recovery:
                self.error_recovery.report_error(
                    "tof_sensor",
                    "monitor_error",
                    str(e),
                    severity="medium",
                )

    def get_monitor_status(self) -> Dict[str, Any]:
        """Get monitor status for diagnostics."""
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "consecutive_failures": self._consecutive_failures,
            "latest_fill_data": self.get_latest_fill_data(),
            "check_interval_secs": self.cfg.check_interval_secs,
        }
