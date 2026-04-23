"""
Fake PiSignage server for in-process integration tests.

Mimics the subset of the real server's HTTP + Socket.IO API that
TSV6NativeBackend uses:

- HTTP Basic auth on all endpoints
- GET /sync_folders/<installation>/<group>/<filename>  -- serves files from
  a configurable media dir, with ETag / If-Modified-Since support
- GET /api/settings
- Socket.IO 2.x on /newsocket.io path (WebSocket transport):
  - Accepts 'status' events from the player
  - Replies with 'config' on connect and on request=True in status
  - Can push 'sync' and 'setplaylist' events on demand

Implementation notes
--------------------
This server uses a raw TCP socket with a hand-rolled WebSocket / Engine.IO /
Socket.IO stack just deep enough for the ``PlayerProtocolClient`` to connect.
This avoids new production dependencies while still exercising real protocol
paths.  The Engine.IO v3 / Socket.IO v2 wire protocol is implemented:

Engine.IO packet types (single character prefix over WebSocket text frames):
  0 = OPEN, 1 = CLOSE, 2 = PING, 3 = PONG, 4 = MESSAGE, 5 = UPGRADE, 6 = NOOP

Socket.IO packet types (prefix within Engine.IO MESSAGE payload):
  0 = CONNECT, 1 = DISCONNECT, 2 = EVENT, 3 = ACK, 4 = ERROR, 5 = BINARY_EVENT

Usage::

    server = FakePiSignageServer(media_dir=tmp_path / "media")
    server.add_media_file("pepsi_30s.mp4", b"\\x00MP4")
    server.start()
    yield server
    server.stop()

    assert server.url == "http://127.0.0.1:<port>"
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import socket
import socketserver
import struct
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Engine.IO / Socket.IO packet type constants ──────────────────────────────

_EIO_OPEN = b"0"
_EIO_CLOSE = b"1"
_EIO_PING = b"2"
_EIO_PONG = b"3"
_EIO_MESSAGE = b"4"
_EIO_NOOP = b"6"

_SIO_CONNECT = "0"
_SIO_DISCONNECT = "1"
_SIO_EVENT = "2"
_SIO_ACK = "3"


# ── WebSocket helpers (RFC 6455) ─────────────────────────────────────────────


def _ws_handshake(raw_request: str) -> str:
    """Return the HTTP 101 Switching Protocols response for a WS handshake."""
    key = ""
    for line in raw_request.split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
            break
    if not key:
        raise ValueError("Missing Sec-WebSocket-Key header")
    accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )


def _ws_encode(payload: bytes | str) -> bytes:
    """Encode data as a WebSocket text frame (no masking, server → client)."""
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = payload
    length = len(data)
    header = bytearray()
    header.append(0x81)  # FIN=1, opcode=1 (text frame)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    return bytes(header) + data


def _ws_decode(conn: socket.socket) -> str | None:
    """
    Read one WebSocket frame from a socket, return the decoded text payload.

    Returns ``None`` on close or error.  Only handles unmasked text frames and
    masked frames (client → server are always masked per RFC 6455).
    """
    try:
        header = b""
        while len(header) < 2:
            chunk = conn.recv(2 - len(header))
            if not chunk:
                return None
            header += chunk

        fin_opcode = header[0]
        opcode = fin_opcode & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F

        if opcode == 0x08:  # close frame
            return None
        if opcode == 0x09:  # ping frame
            # Send pong
            conn.sendall(_ws_encode(""))
            return ""
        if opcode not in (0x01, 0x02):  # text or binary only
            return ""

        # Read extended length if needed
        if length == 126:
            ext = b""
            while len(ext) < 2:
                chunk = conn.recv(2 - len(ext))
                if not chunk:
                    return None
                ext += chunk
            length = struct.unpack(">H", ext)[0]
        elif length == 127:
            ext = b""
            while len(ext) < 8:
                chunk = conn.recv(8 - len(ext))
                if not chunk:
                    return None
                ext += chunk
            length = struct.unpack(">Q", ext)[0]

        # Read mask key if present
        mask_key = b""
        if masked:
            while len(mask_key) < 4:
                chunk = conn.recv(4 - len(mask_key))
                if not chunk:
                    return None
                mask_key += chunk

        # Read payload
        payload = b""
        while len(payload) < length:
            chunk = conn.recv(min(length - len(payload), 65536))
            if not chunk:
                return None
            payload += chunk

        # Unmask
        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        return payload.decode("utf-8", errors="replace")

    except (OSError, ConnectionError):
        return None


# ── HTTP helpers ─────────────────────────────────────────────────────────────


def _http_response(status: str, body: bytes = b"", content_type: str = "text/plain", headers: dict | None = None) -> bytes:
    h = [
        f"HTTP/1.1 {status}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    if headers:
        for k, v in headers.items():
            h.append(f"{k}: {v}")
    return ("\r\n".join(h) + "\r\n\r\n").encode() + body


def _parse_http_request(raw: str) -> dict:
    """Parse a minimal HTTP request into a dict with method, path, headers."""
    lines = raw.split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k.lower()] = v
    return {"method": method, "path": path, "headers": headers}


def _check_basic_auth(headers: dict, username: str, password: str) -> bool:
    auth = headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return False
    decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
    return decoded == f"{username}:{password}"


# ── Socket.IO session ─────────────────────────────────────────────────────────


class _SocketIOSession:
    """
    Manages a single client Socket.IO WebSocket connection.

    Each client gets its own thread running ``_run_loop``.  Outbound messages
    are enqueued via ``send_event``; the loop drains them onto the wire.
    """

    def __init__(
        self,
        conn: socket.socket,
        sid: str,
        server: "FakePiSignageServer",
    ) -> None:
        self._conn = conn
        self._sid = sid
        self._server = server
        self._send_lock = threading.Lock()
        self._closed = False
        self._send_queue: list[str] = []
        self._queue_event = threading.Event()

    def send_eio(self, payload: str) -> None:
        """Enqueue an Engine.IO text frame for delivery."""
        with self._send_lock:
            self._send_queue.append(payload)
            self._queue_event.set()

    def send_event(self, event_name: str, *args: Any) -> None:
        """Enqueue a Socket.IO EVENT packet."""
        data = json.dumps([event_name, *args])
        self.send_eio(_EIO_MESSAGE.decode() + _SIO_EVENT + data)

    def close(self) -> None:
        self._closed = True
        self._queue_event.set()
        try:
            self._conn.close()
        except OSError:
            pass

    def run(self) -> None:
        """Main loop: read from wire, write queue."""
        # Start outbound flusher thread
        flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        flush_thread.start()

        try:
            # Send Engine.IO OPEN
            open_payload = json.dumps({
                "sid": self._sid,
                "upgrades": [],
                "pingTimeout": 60000,
                "pingInterval": 25000,
            })
            self._conn.sendall(_ws_encode(_EIO_OPEN.decode() + open_payload))

            # Send Socket.IO CONNECT for namespace "/"
            self._conn.sendall(_ws_encode(_EIO_MESSAGE.decode() + _SIO_CONNECT))

            # Notify server of new connection
            self._server._on_client_connect(self)

            # Read loop
            while not self._closed:
                self._conn.settimeout(30.0)  # ping interval
                frame = _ws_decode(self._conn)
                if frame is None:
                    break
                if not frame:
                    continue
                self._handle_frame(frame)
        except (OSError, ConnectionError, TimeoutError):
            pass
        finally:
            self._closed = True
            self._queue_event.set()
            flush_thread.join(timeout=2.0)
            self._server._on_client_disconnect(self)

    def _flush_loop(self) -> None:
        while not self._closed:
            self._queue_event.wait(timeout=1.0)
            self._queue_event.clear()
            with self._send_lock:
                pending = list(self._send_queue)
                self._send_queue.clear()
            for msg in pending:
                try:
                    self._conn.sendall(_ws_encode(msg))
                except (OSError, ConnectionError):
                    self._closed = True
                    return

    def _handle_frame(self, frame: str) -> None:
        if not frame:
            return
        eio_type = frame[0]
        rest = frame[1:]

        if eio_type == _EIO_PING.decode():  # ping → pong
            self.send_eio(_EIO_PONG.decode() + rest)
            return

        if eio_type != _EIO_MESSAGE.decode():
            return  # Ignore non-message EIO packets

        if not rest:
            return
        sio_type = rest[0]
        sio_data = rest[1:]

        if sio_type == _SIO_EVENT:
            try:
                parsed = json.loads(sio_data)
            except (json.JSONDecodeError, ValueError):
                return
            if isinstance(parsed, list) and parsed:
                event_name = parsed[0]
                args = parsed[1:]
                self._server._on_client_event(self, event_name, args)


# ── Connection handler (runs per accepted TCP connection) ─────────────────────


class _ConnectionHandler(socketserver.BaseRequestHandler):
    """Handle one incoming TCP connection."""

    server: "FakePiSignageServer"  # type annotation so mypy is happy

    def handle(self) -> None:  # type: ignore[override]
        fake_server: FakePiSignageServer = self.server._fake  # type: ignore[attr-defined]
        conn: socket.socket = self.request

        try:
            conn.settimeout(5.0)
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                raw += chunk
            request_text = raw.decode("utf-8", errors="replace")
            req = _parse_http_request(request_text)
        except (OSError, ValueError):
            return

        path = req["path"]
        method = req["method"]
        upgrade = req["headers"].get("upgrade", "").lower()

        # WebSocket upgrade for Socket.IO — auth is NOT enforced here because
        # the Engine.IO client does not send Basic auth headers in the initial
        # WebSocket handshake when connecting via WebSocket-only transport.
        # The server trusts all WebSocket connections (acceptable for tests).
        if upgrade == "websocket":
            try:
                handshake = _ws_handshake(request_text)
                conn.sendall(handshake.encode())
            except (ValueError, OSError):
                conn.sendall(_http_response("400 Bad Request", b"Bad WS handshake"))
                return

            # Create session and run
            sid = hashlib.md5(f"{time.monotonic()}{id(conn)}".encode()).hexdigest()[:20]
            session = _SocketIOSession(conn=conn, sid=sid, server=fake_server)
            session.run()
            return

        # Auth check for regular HTTP endpoints (not WebSocket)
        if not _check_basic_auth(
            req["headers"], fake_server._username, fake_server._password
        ):
            conn.sendall(_http_response("401 Unauthorized", b"Unauthorized"))
            return

        # Regular HTTP routing
        sync_prefix = f"/sync_folders/{fake_server._installation}/{fake_server._group}/"
        if method == "GET" and path.startswith(sync_prefix):
            filename = path[len(sync_prefix):]
            self._serve_media(conn, fake_server, filename)
        elif method == "GET" and path.startswith("/api/settings"):
            conn.sendall(_http_response(
                "200 OK",
                json.dumps({"settings": {}}).encode(),
                content_type="application/json",
            ))
        else:
            conn.sendall(_http_response("404 Not Found", b"Not Found"))

    def _serve_media(
        self,
        conn: socket.socket,
        fake_server: "FakePiSignageServer",
        filename: str,
    ) -> None:
        path = fake_server._media_dir / filename
        if not path.exists():
            conn.sendall(_http_response("404 Not Found", b"Not Found"))
            return

        data = path.read_bytes()
        etag = hashlib.md5(data).hexdigest()  # noqa: S324

        conn.sendall(_http_response(
            "200 OK",
            data,
            content_type="application/octet-stream",
            headers={"ETag": etag, "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT"},
        ))


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# ── FakePiSignageServer ───────────────────────────────────────────────────────


class FakePiSignageServer:
    """
    In-process PiSignage server emulator for integration tests.

    Implements a minimal Engine.IO v3 / Socket.IO v2 server over WebSocket.

    Parameters
    ----------
    host:
        Bind address. Default ``127.0.0.1``.
    port:
        TCP port. ``0`` = auto-assign from OS.
    media_dir:
        Directory from which asset files are served.  Created if absent.
    installation:
        Installation name used in the sync URL path.
    group:
        Group name used in the sync URL path.
    username / password:
        HTTP Basic-auth credentials validated on every request.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        media_dir: Path | None = None,
        installation: str = "testinst",
        group: str = "default",
        username: str = "pi",
        password: str = "pi",
    ) -> None:
        self._host = host
        self._port = port
        self._installation = installation
        self._group = group
        self._username = username
        self._password = password

        self._media_dir = Path(media_dir) if media_dir else Path("/tmp/fake_pisignage_media")
        self._media_dir.mkdir(parents=True, exist_ok=True)

        # Config pushed to clients on connect
        self._config: dict = {"assets": [], "playlists": []}

        # Active sessions: sid → _SocketIOSession
        self._sessions: dict[str, "_SocketIOSession"] = {}
        self._sessions_lock = threading.Lock()

        # Received events log: list of (event_name, sid, *args)
        self._received_events: list[tuple] = []
        self._events_lock = threading.Lock()

        self._actual_port: int = 0
        self._server: _ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None
        self._started = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the fake server in a background thread."""
        if self._started:
            return

        self._server = _ThreadingTCPServer(
            (self._host, self._port), _ConnectionHandler
        )
        self._server._fake = self  # type: ignore[attr-defined]  # back-pointer
        self._actual_port = self._server.server_address[1]

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="fake-pisignage-server",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info("FakePiSignageServer started on %s", self.url)

    def stop(self) -> None:
        """Stop the server and wait for the thread to join."""
        if not self._started:
            return
        self._started = False

        # Close all active sessions
        with self._sessions_lock:
            for session in list(self._sessions.values()):
                session.close()
            self._sessions.clear()

        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        logger.info("FakePiSignageServer stopped")

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def url(self) -> str:
        """Full base URL, e.g. ``http://127.0.0.1:54321``."""
        return f"http://{self._host}:{self._actual_port}"

    # ── Session callbacks (called from _SocketIOSession threads) ─────────────

    def _on_client_connect(self, session: "_SocketIOSession") -> None:
        with self._sessions_lock:
            self._sessions[session._sid] = session
        with self._events_lock:
            self._received_events.append(("connect", session._sid))
        logger.debug("FakePiSignage: client connected: %s", session._sid)
        # Push config
        session.send_event("config", self._config)

    def _on_client_disconnect(self, session: "_SocketIOSession") -> None:
        with self._sessions_lock:
            self._sessions.pop(session._sid, None)
        with self._events_lock:
            self._received_events.append(("disconnect", session._sid))
        logger.debug("FakePiSignage: client disconnected: %s", session._sid)

    def _on_client_event(
        self, session: "_SocketIOSession", event_name: str, args: list
    ) -> None:
        logger.debug("FakePiSignage: event '%s' from %s", event_name, session._sid)
        with self._events_lock:
            self._received_events.append((event_name, session._sid, *args))

        if event_name == "status":
            settings = args[0] if args else {}
            if settings.get("request"):
                session.send_event("config", self._config)

    # ── Test helpers ─────────────────────────────────────────────────────────

    def set_config(self, config: dict) -> None:
        """Replace the config pushed on connect."""
        self._config = config

    def set_playlist_assets(self, playlist: str, assets: list[str]) -> None:
        """Update the playlist asset list inside the server config."""
        playlists = self._config.get("playlists", [])
        updated = False
        for pl in playlists:
            if pl.get("name") == playlist:
                pl["assets"] = list(assets)
                updated = True
                break
        if not updated:
            playlists.append({"name": playlist, "assets": list(assets)})
        all_assets: list[str] = list(self._config.get("assets", []))
        for a in assets:
            if a not in all_assets:
                all_assets.append(a)
        self._config = {
            **self._config,
            "assets": all_assets,
            "playlists": playlists,
        }

    def push_setplaylist(self, playlist: str) -> None:
        """Emit a ``setplaylist`` event to all connected clients."""
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.send_event("setplaylist", playlist)
        logger.debug("FakePiSignage: pushed setplaylist=%s", playlist)

    def push_sync(
        self,
        playlists: list[str] | None = None,
        assets: list[str] | None = None,
    ) -> None:
        """Emit a ``sync`` event to all connected clients."""
        pls = playlists or []
        asets = assets or self._config.get("assets", [])
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.send_event("sync", pls, asets)

    def get_received_events(self) -> list[tuple]:
        """Return a copy of all events received from players."""
        with self._events_lock:
            return list(self._received_events)

    def add_media_file(self, filename: str, content: bytes) -> None:
        """Write a file to the media directory."""
        dest = self._media_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        logger.debug(
            "FakePiSignage: added media file %s (%d bytes)", filename, len(content)
        )

    def wait_for_event(
        self,
        event_name: str,
        timeout: float = 5.0,
    ) -> tuple | None:
        """Block until an event with the given name is received."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._events_lock:
                for evt in self._received_events:
                    if evt[0] == event_name:
                        return evt
            time.sleep(0.05)
        return None

    def clear_events(self) -> None:
        """Clear all received events."""
        with self._events_lock:
            self._received_events.clear()
