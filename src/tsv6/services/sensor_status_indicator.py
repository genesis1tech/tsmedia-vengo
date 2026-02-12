"""
Status Indicator Overlay

Displays three 4px dots stacked vertically in the lower left corner:
- Top: Bin level ToF sensor (VL53L0X) — blue/gray
- Middle: Recycle verification ToF sensor (VL53L1X) — blue/gray
- Bottom: Network connection — green/red

Refreshes every 5 seconds.
"""

import logging
import os
import tkinter as tk
from typing import Callable, Optional

logger = logging.getLogger(__name__)

COLOR_SENSOR_ACTIVE = '#0080FF'    # Blue — sensor connected
COLOR_SENSOR_INACTIVE = '#333333'  # Dark gray — sensor off
COLOR_NET_CONNECTED = '#00FF00'    # Green — network up
COLOR_NET_DISCONNECTED = '#FF0000' # Red — network down


class SensorStatusIndicator:
    """Overlay showing three stacked dots for sensor and network status."""

    DOT_SIZE = 4
    DOT_GAP = 6
    MARGIN_LEFT = 10
    MARGIN_BOTTOM = 10
    REFRESH_INTERVAL_MS = 5000

    def __init__(
        self,
        bin_level_check: Optional[Callable[[], bool]] = None,
        recycle_check: Optional[Callable[[], bool]] = None,
        network_check: Optional[Callable[[], bool]] = None,
    ):
        self._bin_level_check = bin_level_check or (lambda: False)
        self._recycle_check = recycle_check or (lambda: False)
        self._network_check = network_check or (lambda: False)

        self.root: Optional[tk.Tk] = None
        self.canvas: Optional[tk.Canvas] = None
        self._dot_bin: Optional[int] = None
        self._dot_recycle: Optional[int] = None
        self._dot_network: Optional[int] = None
        self.running = False

        if not os.environ.get('DISPLAY'):
            if os.path.exists('/tmp/.X11-unix/X0'):
                os.environ['DISPLAY'] = ':0'

    # Background color used as transparent key — must not match any dot color
    _BG_KEY = '#010101'

    def _create_window(self):
        self.root = tk.Tk()
        self.root.title('')
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)
        self.root.config(bg=self._BG_KEY)

        self.root.wait_visibility(self.root)
        try:
            self.root.wm_attributes('-transparentcolor', self._BG_KEY)
        except tk.TclError:
            pass

        screen_height = self.root.winfo_screenheight()

        step = self.DOT_SIZE + self.DOT_GAP
        win_w = self.DOT_SIZE
        win_h = step * 3 - self.DOT_GAP  # 3 dots, 2 gaps

        x_pos = self.MARGIN_LEFT
        y_pos = screen_height - win_h - self.MARGIN_BOTTOM

        self.root.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")

        self.canvas = tk.Canvas(
            self.root, width=win_w, height=win_h,
            highlightthickness=0, bg=self._BG_KEY,
        )
        self.canvas.pack()

        # Top dot — bin level sensor
        self._dot_bin = self.canvas.create_oval(
            0, 0, self.DOT_SIZE, self.DOT_SIZE,
            fill=COLOR_SENSOR_INACTIVE, outline=COLOR_SENSOR_INACTIVE,
        )
        # Middle dot — recycle sensor
        y1 = step
        self._dot_recycle = self.canvas.create_oval(
            0, y1, self.DOT_SIZE, y1 + self.DOT_SIZE,
            fill=COLOR_SENSOR_INACTIVE, outline=COLOR_SENSOR_INACTIVE,
        )
        # Bottom dot — network
        y2 = step * 2
        self._dot_network = self.canvas.create_oval(
            0, y2, self.DOT_SIZE, y2 + self.DOT_SIZE,
            fill=COLOR_NET_DISCONNECTED, outline=COLOR_NET_DISCONNECTED,
        )

        logger.info(f"Status indicator created at ({x_pos}, {y_pos})")

    def _update(self):
        if not self.running:
            return

        try:
            bin_c = COLOR_SENSOR_ACTIVE if self._bin_level_check() else COLOR_SENSOR_INACTIVE
            rec_c = COLOR_SENSOR_ACTIVE if self._recycle_check() else COLOR_SENSOR_INACTIVE
            net_c = COLOR_NET_CONNECTED if self._network_check() else COLOR_NET_DISCONNECTED

            if self.canvas:
                self.canvas.itemconfig(self._dot_bin, fill=bin_c, outline=bin_c)
                self.canvas.itemconfig(self._dot_recycle, fill=rec_c, outline=rec_c)
                self.canvas.itemconfig(self._dot_network, fill=net_c, outline=net_c)
                self.root.lift()
        except Exception as e:
            logger.error(f"Status indicator update error: {e}")

        if self.running and self.root:
            self.root.after(self.REFRESH_INTERVAL_MS, self._update)

    def run(self):
        """Run the indicator (blocking — call from a daemon thread)."""
        logger.info("Starting status indicator")
        self.running = True
        try:
            self._create_window()
            self.root.after(100, self._update)
            self.root.mainloop()
        except Exception as e:
            logger.error(f"Status indicator error: {e}")
        finally:
            self.running = False

    def stop(self):
        logger.info("Stopping status indicator")
        self.running = False
        if self.root:
            try:
                self.root.quit()
                self.root.destroy()
            except Exception:
                pass
