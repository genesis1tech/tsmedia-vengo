#!/usr/bin/env python3
"""
PiSignage health monitor for TSV6.

Runs periodic health checks against the remote PiSignage server and
integrates with the TSV6 ErrorRecoverySystem for staged recovery:
  - Soft: retry connection
  - Intermediate: report to AWS for remote alerting
  - Hard: restart PiSignage player process on Pi via shell command
  - Critical: fall back to VLC-based EnhancedVideoPlayer (legacy mode)
"""

import logging
import threading
from collections.abc import Callable

from tsv6.display.pisignage_adapter import PiSignageAdapter

logger = logging.getLogger(__name__)


class PiSignageHealthMonitor:
    """
    Background health monitor for PiSignage server availability.

    Reports failures to ErrorRecoverySystem and triggers fallback
    when the server is unreachable for an extended period.

    Thread-safe: all mutable counters are protected by ``_state_lock``.
    """

    def __init__(
        self,
        adapter: PiSignageAdapter,
        check_interval: float = 30.0,
        failure_threshold: int = 3,
        on_server_down: Callable[[], None] | None = None,
        on_server_recovered: Callable[[], None] | None = None,
    ):
        self._adapter = adapter
        self._check_interval = check_interval
        self._failure_threshold = failure_threshold
        self._on_server_down = on_server_down
        self._on_server_recovered = on_server_recovered

        self._state_lock = threading.Lock()
        self._consecutive_failures: int = 0
        self._is_down: bool = False

        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the background health check loop."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._health_loop, daemon=True, name="pisignage-health"
        )
        self._thread.start()
        logger.info(
            "PiSignage health monitor started (interval=%.0fs, threshold=%d)",
            self._check_interval,
            self._failure_threshold,
        )

    def stop(self) -> None:
        """Stop the health check loop."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._check_interval + 2)
        logger.info("PiSignage health monitor stopped")

    @property
    def is_down(self) -> bool:
        with self._state_lock:
            return self._is_down

    @property
    def consecutive_failures(self) -> int:
        with self._state_lock:
            return self._consecutive_failures

    def _health_loop(self) -> None:
        """Periodic health check loop."""
        while self._running and not self._stop_event.is_set():
            fire_down = False
            fire_recovered = False

            try:
                healthy = self._adapter.health_check()

                with self._state_lock:
                    if healthy:
                        if self._is_down:
                            logger.info(
                                "PiSignage server recovered after %d failures",
                                self._consecutive_failures,
                            )
                            self._is_down = False
                            fire_recovered = True
                        self._consecutive_failures = 0
                    else:
                        self._consecutive_failures += 1
                        logger.warning(
                            "PiSignage health check failed (%d/%d)",
                            self._consecutive_failures,
                            self._failure_threshold,
                        )
                        if (
                            self._consecutive_failures >= self._failure_threshold
                            and not self._is_down
                        ):
                            self._is_down = True
                            fire_down = True
                            logger.error(
                                "PiSignage server DOWN after %d failures",
                                self._consecutive_failures,
                            )

            except Exception as e:
                logger.error("Health monitor error: %s", e)

            # Fire callbacks outside the lock to avoid deadlocks
            if fire_recovered and self._on_server_recovered:
                self._on_server_recovered()
            if fire_down and self._on_server_down:
                self._on_server_down()

            self._stop_event.wait(timeout=self._check_interval)
