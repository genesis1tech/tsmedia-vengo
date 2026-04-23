"""
Chromium kiosk lifecycle manager and CDP helper for the TSV6 renderer.

Responsibilities
----------------
- Launch ``chromium-browser`` in kiosk mode with the exact flags required for
  the Waveshare 7" DSI display (800x480).
- Suppress the "session restore" prompt by patching the Chromium Preferences
  file before launch.
- Communicate with the running browser via the Chrome DevTools Protocol (CDP)
  over a plain WebSocket connection (uses ``websocket-client``, which is
  already a project dependency).
- Provide ``reload``, ``navigate``, ``get_zone_rect``, and ``is_running``
  helpers.

CDP is used sparingly: only ``Page.reload``, ``Page.navigate``, and
``DOM.getBoxModel`` are exercised.  No PyChromeDevTools library is added;
instead the small amount of JSON-RPC plumbing is inlined here.

Thread-safety
-------------
``_cdp_send`` acquires ``_cdp_lock`` around the WebSocket send/recv cycle.
All other methods are designed to be callable from multiple threads, though
in practice only the orchestration thread calls them.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Constants                                                                   #
# --------------------------------------------------------------------------- #

_CHROMIUM_BINARY = "chromium-browser"
_CDP_CONNECT_RETRIES = 20
_CDP_CONNECT_DELAY = 0.5      # seconds between retries
_CDP_TIMEOUT = 5.0            # seconds for a single CDP round-trip
_SIGKILL_GRACE = 5.0          # seconds between SIGTERM and SIGKILL


# --------------------------------------------------------------------------- #
#  ChromiumKiosk                                                               #
# --------------------------------------------------------------------------- #

class ChromiumKiosk:
    """
    Manages a single Chromium kiosk process and its CDP connection.

    Parameters
    ----------
    url:
        Initial URL to open (typically the RouterServer URL).
    display:
        X display string, e.g. ``":0"``.
    xauthority:
        Path to the ``.Xauthority`` file for the X session.
    user_data_dir:
        Chromium user data directory.  Will be created if absent.
    cdp_port:
        Remote debugging port.  Must not conflict with other processes.
    width:
        Display width in pixels.
    height:
        Display height in pixels.
    """

    def __init__(
        self,
        url: str,
        display: str = ":0",
        xauthority: str = "/home/pi/.Xauthority",
        user_data_dir: Path = Path("/home/pi/.config/tsv6-chromium"),
        cdp_port: int = 9222,
        width: int = 800,
        height: int = 1280,
    ) -> None:
        self._url = url
        self._display = display
        self._xauthority = xauthority
        self._user_data_dir = user_data_dir
        self._cdp_port = cdp_port
        self._width = width
        self._height = height

        self._process: subprocess.Popen[bytes] | None = None
        self._ws_url: str | None = None      # webSocketDebuggerUrl
        self._cdp_id = 0
        self._cdp_lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> bool:
        """
        Launch Chromium with kiosk flags.

        Returns ``True`` if the process started and CDP became reachable,
        ``False`` otherwise.
        """
        self._patch_preferences()

        env = dict(os.environ)
        env["DISPLAY"] = self._display
        env["XAUTHORITY"] = self._xauthority

        cmd = self._build_command()
        logger.info("Launching Chromium: %s", " ".join(cmd))

        try:
            self._process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.error(
                "chromium-browser not found.  Install with: "
                "sudo apt install chromium-browser"
            )
            return False

        logger.info("Chromium PID %d started.", self._process.pid)
        return self._wait_for_cdp()

    def stop(self) -> None:
        """Send SIGTERM to Chromium, then SIGKILL if it does not exit."""
        if self._process is None:
            return
        pid = self._process.pid
        logger.info("Stopping Chromium (PID %d).", pid)
        try:
            self._process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self._process.wait(timeout=_SIGKILL_GRACE)
        except subprocess.TimeoutExpired:
            logger.warning("Chromium did not exit; sending SIGKILL.")
            self._process.kill()
            self._process.wait()
        finally:
            self._process = None
            self._ws_url = None

    def reload(self) -> None:
        """Reload the current page via CDP ``Page.reload``."""
        self._cdp_send("Page.reload", {})

    def navigate(self, url: str) -> None:
        """Navigate to *url* via CDP ``Page.navigate``."""
        self._cdp_send("Page.navigate", {"url": url})

    def is_running(self) -> bool:
        """Return ``True`` if the Chromium process is alive."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def get_zone_rect(self, selector: str) -> tuple[int, int, int, int] | None:
        """
        Return the bounding box ``(x, y, width, height)`` of the element
        matching *selector* (CSS selector), using CDP ``DOM.getBoxModel``.

        Returns ``None`` if the element cannot be found or CDP is unavailable.
        """
        try:
            # Step 1 — resolve the selector to a nodeId.
            doc_result = self._cdp_send("DOM.getDocument", {"depth": 0})
            root_node_id = (
                doc_result.get("result", {})
                .get("root", {})
                .get("nodeId")
            )
            if root_node_id is None:
                return None

            sel_result = self._cdp_send(
                "DOM.querySelector",
                {"nodeId": root_node_id, "selector": selector},
            )
            node_id = sel_result.get("result", {}).get("nodeId")
            if not node_id:
                return None

            # Step 2 — get the box model for the node.
            box_result = self._cdp_send("DOM.getBoxModel", {"nodeId": node_id})
            model = box_result.get("result", {}).get("model", {})
            content = model.get("content")
            if not content or len(content) < 8:
                return None

            # content is [x1,y1, x2,y2, x3,y3, x4,y4] (clockwise from TL).
            x = int(content[0])
            y = int(content[1])
            w = int(content[4] - content[0])
            h = int(content[5] - content[1])
            return (x, y, w, h)

        except Exception as exc:
            logger.warning("get_zone_rect(%r) failed: %s", selector, exc)
            return None

    # ── Command-line builder ───────────────────────────────────────────────

    def _build_command(self) -> list[str]:
        """Return the Chromium command as a list of strings."""
        return [
            _CHROMIUM_BINARY,
            "--kiosk",
            "--incognito",
            "--noerrdialogs",
            "--disable-infobars",
            "--disable-session-crashed-bubble",
            "--disable-features=TranslateUI,InfiniteSessionRestore",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-fullscreen",
            "--window-position=0,0",
            f"--window-size={self._width},{self._height}",
            "--hide-scrollbars",
            "--overscroll-history-navigation=0",
            "--disable-pinch",
            "--autoplay-policy=no-user-gesture-required",
            # Touch support — Goodix on Waveshare DSI only dispatches JS touch/pointer
            # events when touch-events is explicitly enabled on Linux/X11.
            "--touch-events=enabled",
            "--enable-features=TouchpadAndWheelScrollLatching",
            "--password-store=basic",
            "--disk-cache-dir=/tmp/chromium-cache",
            "--disk-cache-size=52428800",
            f"--user-data-dir={self._user_data_dir}",
            f"--remote-debugging-port={self._cdp_port}",
            f"--remote-allow-origins=http://localhost:{self._cdp_port}",
            self._url,
        ]

    # ── Preferences patch ──────────────────────────────────────────────────

    def _patch_preferences(self) -> None:
        """
        Write ``exited_cleanly=true`` into the Chromium Preferences file so
        that the browser does not display the "session restore" prompt.
        """
        prefs_path = self._user_data_dir / "Default" / "Preferences"
        try:
            prefs_path.parent.mkdir(parents=True, exist_ok=True)
            if prefs_path.exists():
                raw = prefs_path.read_text(encoding="utf-8")
                try:
                    prefs: dict[str, Any] = json.loads(raw)
                except json.JSONDecodeError:
                    prefs = {}
            else:
                prefs = {}

            profile = prefs.setdefault("profile", {})
            profile["exited_cleanly"] = True
            profile["exit_type"] = "Normal"

            prefs_path.write_text(
                json.dumps(prefs, indent=2),
                encoding="utf-8",
            )
            logger.debug("Patched Chromium Preferences at %s", prefs_path)
        except OSError as exc:
            logger.warning("Could not patch Chromium Preferences: %s", exc)

    # ── CDP plumbing ───────────────────────────────────────────────────────

    def _wait_for_cdp(self) -> bool:
        """
        Poll the CDP ``/json/list`` endpoint until the tab list is available,
        then capture the ``webSocketDebuggerUrl`` for the first tab.

        Returns ``True`` on success.
        """
        import urllib.request  # stdlib; no extra deps

        endpoint = f"http://localhost:{self._cdp_port}/json/list"
        for attempt in range(_CDP_CONNECT_RETRIES):
            try:
                with urllib.request.urlopen(endpoint, timeout=1.0) as resp:
                    tabs = json.loads(resp.read().decode())
                    if tabs:
                        self._ws_url = tabs[0].get("webSocketDebuggerUrl")
                        if self._ws_url:
                            logger.info("CDP available: %s", self._ws_url)
                            return True
            except Exception:
                pass
            time.sleep(_CDP_CONNECT_DELAY)

        logger.error("CDP did not become reachable after %d attempts.", _CDP_CONNECT_RETRIES)
        return False

    def _cdp_send(self, method: str, params: dict) -> dict:
        """
        Send a single CDP JSON-RPC request and return the response dict.

        Opens a fresh WebSocket connection for each call to keep the
        implementation simple and avoid managing a persistent socket.
        """
        if not self._ws_url:
            raise RuntimeError("CDP not connected; call start() first.")

        import websocket  # websocket-client, added by Agent A deps

        with self._cdp_lock:
            self._cdp_id += 1
            msg_id = self._cdp_id
            payload = json.dumps(
                {"id": msg_id, "method": method, "params": params}
            )

            ws = websocket.create_connection(
                self._ws_url,
                timeout=_CDP_TIMEOUT,
            )
            try:
                ws.send(payload)
                while True:
                    raw = ws.recv()
                    response: dict = json.loads(raw)
                    if response.get("id") == msg_id:
                        return response
                    # Ignore events that arrive before our response.
            finally:
                ws.close()
