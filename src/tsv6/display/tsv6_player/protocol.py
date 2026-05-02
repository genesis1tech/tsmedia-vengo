"""
Socket.IO protocol client for TSV6 player.

Implements the PiSignage Socket.IO 2.x wire protocol used by pisignage-server.
The server runs Socket.IO 2.4 at /newsocket.io; this client pins
python-socketio<5 to ensure compatibility.

Thread-safety: all mutable state is guarded by ``_lock``.
"""

import base64
import collections
import logging
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import socketio

logger = logging.getLogger(__name__)

_PLAYER_VERSION = "2.0.0"
_PLATFORM_VERSION = "tsv6-1.0"
_STATUS_QUEUE_MAX = 500


def _local_ip() -> str:
    """Return the best-guess local IP address, falling back to 0.0.0.0."""
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "0.0.0.0"


class PlayerProtocolClient:
    """
    Socket.IO client that speaks the PiSignage player protocol.

    Parameters
    ----------
    server_url:
        Full URL of the PiSignage server, e.g. ``https://tsmedia.g1tech.cloud``.
    cpu_serial:
        Hardware serial number used as the unique player identifier.
    player_name:
        Human-readable installation name, e.g. ``g1tech26``.
    on_config:
        Called when the server emits ``config``. Receives the raw config dict.
    on_sync:
        Called when the server emits ``sync``. Receives ``(playlists, assets)``
        as lists of names.
    on_setplaylist:
        Called when the server emits ``setplaylist``. Receives the playlist
        name and must return an ack message string.
    on_playlist_media:
        Optional. Called for ``playlist_media`` events (pause/forward/backward).
        Must return a response dict.
    on_shell:
        Optional. Called for ``shell`` events. Must return a response dict.
        Return ``{"err": "not supported"}`` if unsupported.
    on_snapshot:
        Optional. Called for ``snapshot`` events. Must return JPEG bytes.
        Return a blank 1x1 JPEG if screen capture is unavailable.
    """

    def __init__(
        self,
        server_url: str,
        cpu_serial: str,
        player_name: str,
        on_config: Callable[[dict], None],
        on_sync: Callable[..., None],
        on_setplaylist: Callable[[str], str],
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_playlist_media: Callable[[str], dict] | None = None,
        on_shell: Callable[[str], dict] | None = None,
        on_snapshot: Callable[[], bytes] | None = None,
    ) -> None:
        self._server_url = server_url
        self._cpu_serial = cpu_serial
        self._player_name = player_name

        # Callbacks supplied by caller.
        self._on_config = on_config
        self._on_sync = on_sync
        self._on_setplaylist = on_setplaylist
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_playlist_media = on_playlist_media
        self._on_shell = on_shell
        self._on_snapshot = on_snapshot

        # Mutable state — always access under ``_lock``.
        self._lock = threading.Lock()
        self._connected = False
        self._reconnections = 0
        self._events_received = 0
        self._last_status_sent_at: float | None = None
        # Bounded offline queue; only status events are queued.
        self._status_queue: collections.deque[tuple[dict, dict, int]] = (
            collections.deque(maxlen=_STATUS_QUEUE_MAX)
        )

        # Build the Socket.IO client once.
        self._sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,  # retry indefinitely
            reconnection_delay=1,
            reconnection_delay_max=30,
        )
        self._register_handlers()

    # ── Public API ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Establish the Socket.IO connection.

        Returns True if the connection succeeds, False otherwise.
        """
        try:
            self._sio.connect(
                self._server_url,
                socketio_path="/newsocket.io",
                transports=["polling"],
            )
            return True
        except Exception as exc:
            logger.error("Connection to %s failed: %s", self._server_url, exc)
            return False

    def disconnect(self) -> None:
        """Gracefully close the Socket.IO connection."""
        self._sio.disconnect()
        with self._lock:
            self._connected = False
        logger.info("Disconnected from %s", self._server_url)

    def is_connected(self) -> bool:
        """Return True if currently connected."""
        with self._lock:
            return self._connected

    def send_status(self, status: dict, priority: int = 0) -> None:
        """
        Emit a ``status`` event to the server.

        Identity fields (cpuSerialNumber, name, version, platform_version,
        myIpAddress) are merged into the settings object automatically.
        The caller supplies only the runtime ``status`` dict.

        If the client is not connected, the message is placed in a bounded
        offline queue and flushed once the connection is restored.

        Parameters
        ----------
        status:
            Runtime state dict, e.g. ``{"tvStatus": True, ...}``.
        priority:
            1 bypasses the server-side 60 s throttle and triggers an
            immediate config push.
        """
        settings = self._build_settings()
        with self._lock:
            if not self._connected:
                self._status_queue.append((settings, status, priority))
                logger.debug(
                    "Queued status emit (queue depth=%d)", len(self._status_queue)
                )
                return
        self._emit_status(settings, status, priority)

    def request_reconfig(self) -> None:
        """
        Ask the server to push a fresh config immediately.

        Sends a priority-1 status event with ``request=True`` to bypass the
        server's 60 s throttle and trigger a config re-push.
        """
        settings = self._build_settings()
        settings["request"] = True
        with self._lock:
            connected = self._connected
        if not connected:
            # Queue with request flag embedded in settings.
            with self._lock:
                self._status_queue.append((settings, {}, 1))
            return
        self._emit_status(settings, {}, 1)

    def send_upload(self, filename: str, data: bytes) -> None:
        """
        Stub upload — accepts parameters but performs no network I/O.

        The PiSignage protocol supports log upload via the ``upload`` event;
        this implementation is intentionally a no-op.
        """
        logger.debug("send_upload called for %s (%d bytes) — no-op", filename, len(data))

    def get_metrics(self) -> dict:
        """
        Return current operational metrics.

        Keys
        ----
        connected : bool
        last_status_sent_at : float | None
            UNIX timestamp of the most recent status emit, or None.
        events_received : int
            Total inbound events received since instantiation.
        reconnections : int
            Total reconnections completed since instantiation.
        queue_depth : int
            Number of status messages currently waiting in the offline queue.
        """
        with self._lock:
            return {
                "connected": self._connected,
                "last_status_sent_at": self._last_status_sent_at,
                "events_received": self._events_received,
                "reconnections": self._reconnections,
                "queue_depth": len(self._status_queue),
            }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_settings(self) -> dict:
        """Return the identity portion of the settings object."""
        return {
            "cpuSerialNumber": self._cpu_serial,
            "name": self._player_name,
            "version": _PLAYER_VERSION,
            "platform_version": _PLATFORM_VERSION,
            "myIpAddress": _local_ip(),
        }

    def _emit_status(self, settings: dict, status: dict, priority: int) -> None:
        """Emit the status event and update accounting."""
        # socketio.Client.emit treats a 3rd positional arg as `namespace`, not
        # extra data; pack the payload as a single tuple so the server sees
        # status(settings, status, priority) as three positional args.
        self._sio.emit("status", (settings, status, priority))
        with self._lock:
            self._last_status_sent_at = time.time()
        logger.debug("Emitted status (priority=%d)", priority)

    def _flush_queue(self) -> None:
        """Drain the offline status queue after reconnection."""
        with self._lock:
            pending = list(self._status_queue)
            self._status_queue.clear()
        if pending:
            logger.info("Flushing %d queued status event(s)", len(pending))
        for settings, status, priority in pending:
            self._emit_status(settings, status, priority)

    def _increment_events(self) -> None:
        with self._lock:
            self._events_received += 1

    # ── Socket.IO event handlers ──────────────────────────────────────────────

    def _register_handlers(self) -> None:
        """Attach all Socket.IO event handlers to the client instance."""
        sio = self._sio

        @sio.event
        def connect() -> None:  # type: ignore[misc]
            with self._lock:
                was_connected = self._connected
                self._connected = True
                if was_connected:
                    self._reconnections += 1
            logger.info("Connected to %s", self._server_url)
            self._flush_queue()
            if self._on_connect is not None:
                try:
                    self._on_connect()
                except Exception as exc:
                    logger.warning("on_connect callback error: %s", exc)

        @sio.event
        def disconnect() -> None:  # type: ignore[misc]
            with self._lock:
                self._connected = False
            logger.info("Disconnected from %s", self._server_url)
            if self._on_disconnect is not None:
                try:
                    self._on_disconnect()
                except Exception as exc:
                    logger.warning("on_disconnect callback error: %s", exc)

        @sio.event
        def connect_error(data: Any) -> None:  # type: ignore[misc]
            logger.error("Connection error: %s", data)

        @sio.on("config")
        def on_config(config_obj: dict) -> None:
            self._increment_events()
            logger.debug("Received config event")
            self._on_config(config_obj)
            self._sio.emit("secret_ack", None)

        @sio.on("sync")
        def on_sync(*args: Any) -> None:
            self._increment_events()
            logger.debug("Received sync event with %d args", len(args))
            # Protocol: sync(playlists, assets, ticker, logo, logox, logoy,
            #               combineDefault, omxVolume, loadOnCompletion,
            #               assetsValidity)  — 11 positional args.
            playlists: list[str] = list(args[0]) if len(args) > 0 else []
            assets: list[str] = list(args[1]) if len(args) > 1 else []
            ticker: dict = args[2] if len(args) > 2 and isinstance(args[2], dict) else {}
            self._on_sync(playlists, assets, ticker)

        @sio.on("setplaylist")
        def on_setplaylist(playlist_name: str) -> None:
            self._increment_events()
            logger.debug("Received setplaylist: %s", playlist_name)
            ack_message = self._on_setplaylist(playlist_name)
            self._sio.emit("setplaylist_ack", ack_message)

        @sio.on("playlist_media")
        def on_playlist_media(action: str) -> None:
            self._increment_events()
            logger.debug("Received playlist_media: %s", action)
            if self._on_playlist_media is not None:
                response = self._on_playlist_media(action)
            else:
                response = {"status": "not supported"}
            self._sio.emit("media_ack", response)

        @sio.on("shell")
        def on_shell(cmd_string: str) -> None:
            self._increment_events()
            logger.debug("Received shell command")
            if self._on_shell is not None:
                response = self._on_shell(cmd_string)
            else:
                response = {"err": "not supported"}
            self._sio.emit("shell_ack", response)

        @sio.on("snapshot")
        def on_snapshot() -> None:  # type: ignore[misc]
            self._increment_events()
            logger.debug("Received snapshot request")
            if self._on_snapshot is not None:
                jpeg_bytes = self._on_snapshot()
            else:
                # Minimal valid 1x1 white JPEG.
                jpeg_bytes = _BLANK_JPEG
            b64_data = base64.b64encode(jpeg_bytes).decode("ascii")
            payload = {
                "data": b64_data,
                "playerInfo": {"cpuSerialNumber": self._cpu_serial},
            }
            self._sio.emit("snapshot", payload)

        @sio.on("swupdate")
        def on_swupdate(*args: Any) -> None:
            self._increment_events()
            logger.info("Received swupdate (no-op): %s", args)

        @sio.on("cmd")
        def on_cmd(*args: Any) -> None:
            self._increment_events()
            logger.info("Received cmd (no-op): %s", args)

        @sio.on("upload_ack")
        def on_upload_ack(filename: str) -> None:
            self._increment_events()
            logger.debug("Received upload_ack for %s", filename)


# Minimal valid 1x1 white JPEG (raw bytes) used when no snapshot provider is
# configured.  Generated offline; no I/O at runtime.
_BLANK_JPEG: bytes = bytes(
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
    b"\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa"
    b"\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n"
    b"\x16\x17\x18\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZ"
    b"cdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95"
    b"\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3"
    b"\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca"
    b"\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7"
    b"\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd4P\x00\x00\x00\x1f\xff\xd9"
)
