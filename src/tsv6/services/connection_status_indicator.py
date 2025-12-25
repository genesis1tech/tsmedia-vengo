#!/usr/bin/env python3
"""
Connection Status Indicator Overlay

Displays a small colored dot in the upper right corner of the screen:
- Green: 4G/LTE connected
- Blue: WiFi connected
- Red: Not connected

The dot is 8px and refreshes every 5 seconds.
Runs as a transparent overlay on top of all other windows.

Thread Safety:
- Tkinter GUI runs in the main thread
- Network checks run in a background worker thread
- Worker thread communicates with GUI via thread-safe queue + after()
"""

import os
import sys
import subprocess
import time
import logging
import threading
import queue
import tkinter as tk
from typing import Optional, Tuple

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Connection status colors (RGB hex)
COLOR_4G = '#00FF00'      # Green for 4G/LTE
COLOR_WIFI = '#0080FF'    # Blue for WiFi
COLOR_DISCONNECTED = '#FF0000'  # Red for disconnected


class DisplayNotAvailableError(Exception):
    """Raised when no display is available for the indicator."""
    pass


class ConnectionStatusIndicator:
    """Connection status indicator overlay that shows colored dot based on connectivity.
    
    Thread Safety Design:
    - All Tkinter operations happen in the main thread (via run())
    - Network checks run in a background worker thread
    - Worker thread puts status updates in a queue
    - Main thread polls queue via after() and updates UI
    """
    
    DOT_SIZE = 8
    REFRESH_INTERVAL_MS = 5000  # 5 seconds
    CHECK_INTERVAL_SECS = 5.0   # Network check interval in worker thread
    QUEUE_POLL_MS = 100         # How often to poll the queue from main thread
    MARGIN_RIGHT = 10
    MARGIN_TOP = 10
    
    def __init__(self):
        """Initialize the connection status indicator."""
        self.root: Optional[tk.Tk] = None
        self.canvas: Optional[tk.Canvas] = None
        self.dot_id: Optional[int] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._status_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self.running = False
        self._current_color = COLOR_DISCONNECTED
        self._display_available = False
        self._setup_display()
        
    def _setup_display(self):
        """Setup display environment.
        
        Raises:
            DisplayNotAvailableError: If no display is available
        """
        logger.info(
            "Display environment: DISPLAY=%r XAUTHORITY=%r",
            os.environ.get('DISPLAY'),
            os.environ.get('XAUTHORITY'),
        )
        if not os.environ.get('DISPLAY'):
            if os.path.exists('/tmp/.X11-unix/X0'):
                os.environ['DISPLAY'] = ':0'
            elif os.path.exists('/tmp/.X11-unix/X1'):
                os.environ['DISPLAY'] = ':1'
            else:
                logger.error('No display available')
                # CRITICAL FIX: Raise exception instead of sys.exit(1)
                # sys.exit() raises SystemExit which is not caught by except Exception
                # and will terminate the importing service
                raise DisplayNotAvailableError('No display available')
        self._display_available = True
                
    def _check_lte_status(self) -> bool:
        """Check if LTE/4G is connected."""
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:' + env.get('PATH', '')
        
        try:
            # Check if wwan0 interface exists and is up
            result = subprocess.run(
                ["ip", "link", "show", "wwan0"],
                capture_output=True, text=True, timeout=5, env=env
            )
            if result.returncode == 0 and "state UP" in result.stdout:
                # Verify with ping through wwan0
                ping_result = subprocess.run(
                    ["ping", "-I", "wwan0", "-c", "1", "-W", "2", "8.8.8.8"],
                    capture_output=True, text=True, timeout=5, env=env
                )
                if ping_result.returncode == 0:
                    return True
                    
            # Also check with ModemManager
            result = subprocess.run(
                ["mmcli", "-m", "0"],
                capture_output=True, text=True, timeout=10, env=env
            )
            if result.returncode == 0:
                output = result.stdout.lower()
                if 'connected' in output and 'signal quality' in output:
                    # Check for non-zero signal
                    for line in result.stdout.splitlines():
                        if 'signal quality' in line.lower():
                            parts = line.split(':')
                            if len(parts) >= 2:
                                value = parts[1].strip().replace('%', '').split()[0]
                                try:
                                    if int(value) > 0:
                                        return True
                                except ValueError:
                                    pass
        except Exception as e:
            logger.debug(f"LTE check error: {e}")
            
        return False
        
    def _check_wifi_status(self) -> bool:
        """Check if WiFi is connected."""
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:' + env.get('PATH', '')
        
        try:
            # Check if connected to a WiFi network
            for cmd in (["/usr/sbin/iwgetid", "-r"], ["iwgetid", "-r"]):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env)
                    if result.returncode == 0 and result.stdout.strip():
                        # Verify connectivity with ping through wlan0
                        ping_result = subprocess.run(
                            ["ping", "-I", "wlan0", "-c", "1", "-W", "2", "8.8.8.8"],
                            capture_output=True, text=True, timeout=5, env=env
                        )
                        if ping_result.returncode == 0:
                            return True
                        # WiFi connected but no internet - still show as connected
                        return True
                except FileNotFoundError:
                    continue
                    
            # Fallback: check if wlan0 has an IP
            result = subprocess.run(
                ["ip", "addr", "show", "wlan0"],
                capture_output=True, text=True, timeout=5, env=env
            )
            if result.returncode == 0 and "inet " in result.stdout:
                return True
                
        except Exception as e:
            logger.debug(f"WiFi check error: {e}")
            
        return False
        
    def get_connection_status(self) -> Tuple[str, str]:
        """Get current connection status (blocking - run in worker thread).
        
        Returns:
            Tuple of (color, type) where type is '4g', 'wifi', or 'none'
        """
        # Check 4G first (priority)
        if self._check_lte_status():
            return (COLOR_4G, '4g')
            
        # Then check WiFi
        if self._check_wifi_status():
            return (COLOR_WIFI, 'wifi')
            
        # No connection
        return (COLOR_DISCONNECTED, 'none')
    
    def _worker_loop(self):
        """Background worker thread that performs blocking network checks.
        
        This runs in a separate thread to avoid blocking the Tk event loop.
        Status updates are put in a queue for the main thread to process.
        """
        logger.info("Network check worker thread started")
        
        while not self._stop_event.is_set():
            try:
                # Perform blocking network checks
                color, conn_type = self.get_connection_status()
                
                # Put result in queue for main thread
                self._status_queue.put((color, conn_type))
                
            except Exception as e:
                logger.error(f"Worker thread error: {e}")
            
            # Wait for next check interval or until stopped
            self._stop_event.wait(self.CHECK_INTERVAL_SECS)
        
        logger.info("Network check worker thread stopped")
    
    def _poll_status_queue(self):
        """Poll the status queue and update UI (runs in main thread via after()).
        
        This method is called periodically from the main Tk thread to check
        for status updates from the worker thread and apply them to the UI.
        """
        if not self.running:
            return
        
        try:
            # Process all pending status updates
            while True:
                try:
                    color, conn_type = self._status_queue.get_nowait()
                    
                    if color != self._current_color:
                        logger.info(f"Connection status changed: {conn_type} ({color})")
                        self._current_color = color
                    
                    if self.canvas and self.dot_id:
                        self.canvas.itemconfig(self.dot_id, fill=color, outline=color)
                        
                except queue.Empty:
                    break
                    
        except Exception as e:
            logger.error(f"Error polling status queue: {e}")
        
        # Schedule next poll
        if self.running and self.root:
            self.root.after(self.QUEUE_POLL_MS, self._poll_status_queue)
            
    def _create_window(self):
        """Create the transparent overlay window with dot."""
        self.root = tk.Tk()
        self.root.title('')
        
        # Make window transparent and always on top
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)  # Remove window decorations
        
        # Set up transparent background (works on X11)
        self.root.wait_visibility(self.root)
        try:
            self.root.attributes('-alpha', 1.0)
        except tk.TclError:
            pass
            
        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Calculate position (upper right corner)
        x_pos = screen_width - self.DOT_SIZE - self.MARGIN_RIGHT
        y_pos = self.MARGIN_TOP
        
        # Set window size and position
        self.root.geometry(f"{self.DOT_SIZE}x{self.DOT_SIZE}+{x_pos}+{y_pos}")
        
        # Create canvas for drawing
        self.canvas = tk.Canvas(
            self.root,
            width=self.DOT_SIZE,
            height=self.DOT_SIZE,
            highlightthickness=0,
            bg='black'
        )
        self.canvas.pack()
        
        # Draw the dot
        self.dot_id = self.canvas.create_oval(
            0, 0,
            self.DOT_SIZE, self.DOT_SIZE,
            fill=self._current_color,
            outline=self._current_color
        )
        
        logger.info(f"Indicator window created at ({x_pos}, {y_pos})")
        
    def run(self):
        """Run the connection status indicator (must be called from main thread).
        
        This method:
        1. Creates the Tk window (main thread)
        2. Starts a worker thread for network checks
        3. Runs the Tk mainloop
        """
        if not self._display_available:
            raise DisplayNotAvailableError("Display not available")
            
        logger.info("Starting Connection Status Indicator")
        self.running = True
        self._stop_event.clear()
        
        try:
            self._create_window()
            
            # Start worker thread for network checks
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="ConnectionIndicatorWorker"
            )
            self._worker_thread.start()
            
            # Initial status (will be updated when worker sends first result)
            logger.info(f"Initial indicator color: {self._current_color}")
            
            # Start polling the status queue
            self.root.after(self.QUEUE_POLL_MS, self._poll_status_queue)
            
            # Run the main loop
            self.root.mainloop()
            
        except Exception as e:
            logger.error(f"Error running indicator: {e}")
            raise
        finally:
            self._cleanup()
    
    def _cleanup(self):
        """Internal cleanup (can be called from any thread)."""
        self.running = False
        self._stop_event.set()
        
        # Wait for worker thread to stop
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
            
    def stop(self):
        """Stop the indicator (thread-safe).
        
        This method can be called from any thread. It signals the worker
        thread to stop and schedules the Tk cleanup on the main thread.
        """
        logger.info("Stopping Connection Status Indicator")
        self.running = False
        self._stop_event.set()
        
        if self.root:
            try:
                # Use after_idle to schedule cleanup on the main thread
                # This is thread-safe unlike direct quit()/destroy() calls
                self.root.after_idle(self._destroy_window)
            except Exception as e:
                logger.warning(f"Error scheduling window destroy: {e}")
                # Fallback: try direct destroy (may cause Tcl errors if wrong thread)
                try:
                    self.root.quit()
                except Exception:
                    pass
    
    def _destroy_window(self):
        """Destroy the Tk window (must be called from main thread)."""
        try:
            if self.root:
                self.root.quit()
                self.root.destroy()
                self.root = None
        except Exception as e:
            logger.warning(f"Error destroying window: {e}")


def main():
    """Main entry point."""
    import signal
    
    indicator = ConnectionStatusIndicator()
    
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        indicator.stop()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        indicator.run()
    except KeyboardInterrupt:
        indicator.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
