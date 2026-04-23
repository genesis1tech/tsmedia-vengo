"""
Local Flask router for the TSV6 Chromium-based display renderer.

Responsibilities
----------------
- Serves the HTML layout page at ``/``.
- Serves cached assets (MP4, PNG, JPG, HTML, …) at ``/assets/<filename>``.
- Exposes a Server-Sent Events stream at ``/events`` so the browser receives
  instant show-asset commands without any Chromium navigation.
- Exposes ``/video_zone_rect`` (POST) so the browser can report the pixel
  coordinates of the ``#main`` zone after a ``show_video_zone`` command.
- ``RouterServer.send_command`` enqueues a command dict that the SSE stream
  broadcasts to all connected clients.

Thread-safety
-------------
``_command_queue`` is a ``queue.Queue`` so it is safe to call
``send_command`` from any thread.  The SSE generator drains the queue
on each poll tick.

Usage
-----
::

    server = RouterServer(
        cache_dir=Path("/var/tsv6/assets"),
        layout_html=Path("/opt/tsv6/router_page.html"),
        host="127.0.0.1",
        port=8765,
    )
    server.start()
    server.send_command({"action": "show_idle"})
    ...
    server.stop()
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

from flask import Flask, Response, jsonify, request, send_from_directory

logger = logging.getLogger(__name__)

# How long the SSE generator waits between queue checks (seconds).
_SSE_POLL_INTERVAL: float = 0.05

# How often a keep-alive comment is sent to prevent proxy timeouts (seconds).
_SSE_KEEPALIVE_INTERVAL: float = 15.0


def _sse_event(data: dict) -> str:
    """Serialise *data* as a single SSE ``data:`` line followed by ``\\n\\n``."""
    return f"data: {json.dumps(data)}\n\n"


class RouterServer:
    """
    Lightweight Flask server that drives the Chromium layout via SSE.

    Parameters
    ----------
    cache_dir:
        Directory containing downloaded media assets.  Files are served at
        ``/assets/<filename>``.
    layout_html:
        Absolute path to the router page HTML file (``router_page.html``).
    host:
        Bind address for the Flask development server.  Default ``127.0.0.1``.
    port:
        TCP port.  Default ``8765``.
    """

    def __init__(
        self,
        cache_dir: Path,
        layout_html: Path,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self._cache_dir = cache_dir
        self._layout_html = layout_html
        self._host = host
        self._port = port

        # Unbounded queue; callers should not flood this faster than 100 Hz.
        self._command_queue: queue.Queue[dict] = queue.Queue()

        # Last known pixel rect of the #main zone, set by POST /video_zone_rect
        self._video_zone_rect: tuple[int, int, int, int] | None = None
        self._rect_lock = threading.Lock()

        # Optional callback invoked by POST /api/exit-settings. Used to restart
        # the idle player (VLC) when the user leaves the settings page.
        self._on_wake: "Optional[Callable[[], None]]" = None

        self._app = self._build_app()
        self._server_thread: threading.Thread | None = None
        self._running = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the Flask server in a background daemon thread."""
        if self._running:
            logger.warning("RouterServer is already running.")
            return
        self._running = True
        self._server_thread = threading.Thread(
            target=self._run_server,
            name="router-server",
            daemon=True,
        )
        self._server_thread.start()
        logger.info(
            "RouterServer started at %s (layout=%s, cache_dir=%s)",
            self.url,
            self._layout_html,
            self._cache_dir,
        )

    def stop(self) -> None:
        """Signal the server to stop.  The daemon thread will exit on its own."""
        self._running = False
        logger.info("RouterServer stop requested.")

    def send_command(self, command: dict) -> None:
        """
        Enqueue *command* for delivery to the browser via SSE.

        This method is thread-safe and returns immediately.  The SSE generator
        running inside the request handler will pick up the command on its next
        poll tick (within ``_SSE_POLL_INTERVAL`` seconds).

        Supported command shapes
        -----------------------
        ``{"action": "show_html",       "src": "<asset-filename>"}``
            Load an HTML asset in an iframe inside ``#main``.

        ``{"action": "show_image",      "src": "<asset-filename>"}``
            Inject an ``<img>`` tag into ``#main``.

        ``{"action": "show_product",    "image": "<filename>", "qr_url": "<url>"}``
            Display a product image with a QR-code overlay.

        ``{"action": "show_video_zone", "zone": "main", "rect": [x, y, w, h]}``
            Make ``#main`` transparent so VLC can render behind it.

        ``{"action": "hide_video_zone"}``
            Restore ``#main`` opacity.

        ``{"action": "show_idle"}``
            Return to the idle state (clears ``#main``).
        """
        self._command_queue.put(command)
        logger.debug("Command enqueued: %s", command.get("action"))

    @property
    def url(self) -> str:
        """Base URL of this server, e.g. ``http://127.0.0.1:8765/``."""
        return f"http://{self._host}:{self._port}/"

    def set_wake_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """Install a callback invoked when the user exits the settings page."""
        self._on_wake = callback

    def get_video_zone_rect(self) -> tuple[int, int, int, int] | None:
        """
        Return the most recently reported pixel rect of the video zone.

        The browser POSTs to ``/video_zone_rect`` after receiving a
        ``show_video_zone`` command.  Returns ``None`` until that happens.
        """
        with self._rect_lock:
            return self._video_zone_rect

    # ── Flask application factory ─────────────────────────────────────────────

    def _build_app(self) -> Flask:
        """Construct and configure the Flask application."""
        app = Flask(__name__)
        # Silence the default Werkzeug request logger to keep output clean.
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

        layout_html = self._layout_html
        cache_dir = self._cache_dir
        command_queue = self._command_queue
        rect_lock = self._rect_lock

        @app.route("/")
        def index() -> Response:
            """Serve the router page HTML."""
            return send_from_directory(
                str(layout_html.parent),
                layout_html.name,
                mimetype="text/html",
            )

        @app.route("/assets/<path:filename>")
        def assets(filename: str) -> Response:
            """Serve a cached media asset by filename."""
            return send_from_directory(str(cache_dir), filename)

        @app.route("/events")
        def events() -> Response:
            """
            Server-Sent Events endpoint.

            Streams commands to the connected Chromium page.  Each command is
            serialised as JSON in a ``data:`` field.  Keep-alive comments are
            sent every ``_SSE_KEEPALIVE_INTERVAL`` seconds to prevent proxies
            from closing idle connections.
            """

            def generate() -> Iterator[str]:
                last_keepalive = time.monotonic()
                while True:
                    now = time.monotonic()
                    # Drain all pending commands in one poll cycle.
                    drained_any = False
                    while True:
                        try:
                            cmd = command_queue.get_nowait()
                            yield _sse_event(cmd)
                            drained_any = True
                        except queue.Empty:
                            break
                    # Send keep-alive comment if idle for too long.
                    if not drained_any and (now - last_keepalive) >= _SSE_KEEPALIVE_INTERVAL:
                        yield ": keepalive\n\n"
                        last_keepalive = now
                    time.sleep(_SSE_POLL_INTERVAL)

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        @app.route("/settings")
        def settings_page() -> Response:
            """Serve the touch-first settings/WiFi page."""
            return send_from_directory(
                str(layout_html.parent),
                "settings.html",
                mimetype="text/html",
            )

        @app.route("/api/wifi/status")
        def wifi_status() -> Response:
            """Return the current WiFi connection state via nmcli."""
            try:
                active = subprocess.run(
                    ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY",
                     "device", "wifi", "list", "--rescan", "no"],
                    capture_output=True, text=True, timeout=5,
                )
                current = None
                for line in active.stdout.splitlines():
                    parts = line.split(":")
                    if len(parts) >= 4 and parts[0] == "yes":
                        current = {"ssid": parts[1], "signal": parts[2], "security": parts[3]}
                        break
                ip = subprocess.run(
                    ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", "wlan0"],
                    capture_output=True, text=True, timeout=3,
                )
                ip_addr = ""
                for line in ip.stdout.splitlines():
                    if line.startswith("IP4.ADDRESS"):
                        ip_addr = line.split(":", 1)[1].split("/")[0]
                        break
                return jsonify({"connected": current is not None, "current": current, "ip": ip_addr})
            except Exception as exc:
                return jsonify({"connected": False, "error": str(exc)}), 500

        @app.route("/api/wifi/scan", methods=["POST"])
        def wifi_scan() -> Response:
            """Trigger a rescan and return visible networks."""
            try:
                # Force rescan (non-fatal if it fails — will still return cached results)
                subprocess.run(
                    ["nmcli", "device", "wifi", "rescan"],
                    capture_output=True, text=True, timeout=10,
                )
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE",
                     "device", "wifi", "list"],
                    capture_output=True, text=True, timeout=10,
                )
                seen = {}
                for line in result.stdout.splitlines():
                    # nmcli -t separates with ':' but escapes colons in SSID as '\:'
                    parts = [p.replace("\\:", ":") for p in line.replace("\\:", "\x00").split(":")]
                    parts = [p.replace("\x00", ":") for p in parts]
                    if len(parts) < 4:
                        continue
                    ssid, signal, security, in_use = parts[0], parts[1], parts[2], parts[3]
                    if not ssid:
                        continue
                    # Keep strongest reading per SSID
                    try:
                        sig = int(signal) if signal else 0
                    except ValueError:
                        sig = 0
                    if ssid not in seen or sig > seen[ssid]["signal"]:
                        seen[ssid] = {
                            "ssid": ssid,
                            "signal": sig,
                            "security": security or "--",
                            "in_use": in_use == "*",
                        }
                networks = sorted(seen.values(), key=lambda n: n["signal"], reverse=True)
                return jsonify({"networks": networks})
            except Exception as exc:
                logger.warning("wifi_scan failed: %s", exc)
                return jsonify({"networks": [], "error": str(exc)}), 500

        @app.route("/api/wifi/connect", methods=["POST"])
        def wifi_connect() -> Response:
            """Connect to a WiFi network. Body: {ssid, password}."""
            body = request.get_json(force=True, silent=True) or {}
            ssid = (body.get("ssid") or "").strip()
            password = body.get("password") or ""
            if not ssid:
                return jsonify({"ok": False, "error": "ssid required"}), 400
            try:
                cmd = ["nmcli", "device", "wifi", "connect", ssid]
                if password:
                    cmd += ["password", password]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=45,
                )
                ok = result.returncode == 0
                msg = (result.stdout + result.stderr).strip()
                logger.info("wifi_connect ssid=%s ok=%s msg=%s", ssid, ok, msg[:200])
                return jsonify({"ok": ok, "message": msg})
            except subprocess.TimeoutExpired:
                return jsonify({"ok": False, "error": "timeout"}), 504
            except Exception as exc:
                logger.warning("wifi_connect error: %s", exc)
                return jsonify({"ok": False, "error": str(exc)}), 500

        @app.route("/api/exit-settings", methods=["POST"])
        def exit_settings() -> Response:
            """Resume the idle player (restarts VLC) and redirect browser to /."""
            if server_self._on_wake is not None:
                try:
                    server_self._on_wake()
                except Exception:
                    logger.exception("on_wake callback failed")
            return jsonify({"redirect": "/"})

        @app.route("/video_zone_rect", methods=["POST"])
        def video_zone_rect() -> Response:
            """
            Receive the pixel rect of the ``#main`` zone from the browser.

            Expected body: ``{"rect": [x, y, w, h]}``
            """
            try:
                body = request.get_json(force=True, silent=True) or {}
                rect_raw = body.get("rect", [])
                if len(rect_raw) == 4:
                    rect = (
                        int(rect_raw[0]),
                        int(rect_raw[1]),
                        int(rect_raw[2]),
                        int(rect_raw[3]),
                    )
                    with rect_lock:
                        # mypy: assigning to captured variable via closure trick
                        pass
                    # Store on self via the outer instance reference captured
                    # through the closure over rect_lock.
                    _store_rect(rect)
                    logger.debug("Video zone rect updated: %s", rect)
            except Exception as exc:
                logger.warning("Failed to parse video_zone_rect: %s", exc)
            return Response("{}", mimetype="application/json")

        # Closure helper so the lambda can mutate self._video_zone_rect.
        server_self = self

        def _store_rect(rect: tuple[int, int, int, int]) -> None:
            with server_self._rect_lock:
                server_self._video_zone_rect = rect

        return app

    # ── Server thread ─────────────────────────────────────────────────────────

    def _run_server(self) -> None:
        """Run the Flask development server (blocking)."""
        try:
            self._app.run(
                host=self._host,
                port=self._port,
                threaded=True,
                use_reloader=False,
                debug=False,
            )
        except Exception as exc:
            logger.error("RouterServer crashed: %s", exc)
        finally:
            self._running = False
