"""Kernel-level long-press gesture watcher.

Reads a Linux evdev touchscreen device directly and detects a configurable
hold (default 5 s) anywhere on the screen. Bypasses Chromium's DOM touch
handling, which is unreliable on some X11 + DSI Goodix configurations.

On a confirmed long-press, calls the provided callback exactly once per
hold (re-arms after the touch ends).
"""

from __future__ import annotations

import logging
import os
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# struct input_event on 64-bit Linux: timeval(16B) + u16 type + u16 code + s32 value
_EVENT_FORMAT = "qqHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)

_EV_KEY = 1
_EV_ABS = 3

_BTN_TOUCH = 330
_ABS_X, _ABS_Y = 0, 1
_ABS_MT_POSITION_X, _ABS_MT_POSITION_Y = 53, 54


def _find_touchscreen_device() -> Optional[str]:
    """Return the first /dev/input/event* whose kernel name contains 'touchscreen'."""
    for sys_entry in sorted(Path("/sys/class/input").glob("event*")):
        name_file = sys_entry / "device" / "name"
        try:
            name = name_file.read_text().strip().lower()
        except OSError:
            continue
        if "touchscreen" in name or "goodix" in name:
            return f"/dev/input/{sys_entry.name}"
    return None


class LongPressWatcher:
    """Detects a held-touch gesture of `hold_seconds` on a Linux evdev device."""

    def __init__(
        self,
        on_long_press: Callable[[], None],
        *,
        device_path: Optional[str] = None,
        hold_seconds: float = 5.0,
        drift_units: int = 80,
    ) -> None:
        self._on_long_press = on_long_press
        self._device_path = device_path
        self._hold_seconds = hold_seconds
        self._drift_units = drift_units
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> bool:
        device = self._device_path or _find_touchscreen_device()
        if not device:
            logger.warning("LongPressWatcher: no touchscreen evdev device found")
            return False
        self._device_path = device
        self._thread = threading.Thread(
            target=self._run, name="tsv6-longpress", daemon=True,
        )
        self._thread.start()
        logger.info(
            "LongPressWatcher started (device=%s, hold=%.1fs, drift=%d)",
            device, self._hold_seconds, self._drift_units,
        )
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            fd = os.open(self._device_path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            logger.error("LongPressWatcher: cannot open %s: %s", self._device_path, exc)
            return
        try:
            self._read_loop(fd)
        finally:
            try: os.close(fd)
            except OSError: pass

    def _read_loop(self, fd: int) -> None:
        touching = False
        touch_start_t = 0.0
        start_x = 0
        start_y = 0
        cur_x = 0
        cur_y = 0
        fired = False
        have_anchor = False

        while not self._stop.is_set():
            # Check fire condition on every loop iteration (not just on events)
            # so the callback triggers as soon as hold_seconds elapses.
            if touching and not fired:
                elapsed = time.monotonic() - touch_start_t
                if elapsed >= self._hold_seconds:
                    drift = max(abs(cur_x - start_x), abs(cur_y - start_y))
                    if drift <= self._drift_units:
                        logger.info(
                            "Long-press fired at (%d,%d) after %.1fs (drift=%d)",
                            cur_x, cur_y, elapsed, drift,
                        )
                        fired = True
                        try:
                            self._on_long_press()
                        except Exception:
                            logger.exception("Long-press callback raised")

            try:
                data = os.read(fd, _EVENT_SIZE * 64)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            except OSError as exc:
                logger.warning("LongPressWatcher: read error: %s", exc)
                return
            if not data:
                time.sleep(0.05)
                continue

            for i in range(0, len(data), _EVENT_SIZE):
                chunk = data[i:i + _EVENT_SIZE]
                if len(chunk) < _EVENT_SIZE:
                    break
                _, _, typ, code, val = struct.unpack(_EVENT_FORMAT, chunk)

                if typ == _EV_KEY and code == _BTN_TOUCH:
                    if val == 1:
                        # Press: start tracking, but anchor on the FIRST position
                        # report (after this key event) so we don't measure drift
                        # against a stale cur_x/cur_y from a previous touch.
                        touching = True
                        fired = False
                        have_anchor = False
                        touch_start_t = time.monotonic()
                    else:
                        # Release: cancel any in-flight hold
                        touching = False
                        have_anchor = False

                elif typ == _EV_ABS:
                    if code in (_ABS_X, _ABS_MT_POSITION_X):
                        cur_x = val
                    elif code in (_ABS_Y, _ABS_MT_POSITION_Y):
                        cur_y = val
                    if touching and not have_anchor and cur_x and cur_y:
                        start_x, start_y = cur_x, cur_y
                        have_anchor = True
                    elif touching and have_anchor and not fired:
                        drift = max(abs(cur_x - start_x), abs(cur_y - start_y))
                        if drift > self._drift_units:
                            # Dragged too far: cancel without firing
                            touching = False
                            have_anchor = False
