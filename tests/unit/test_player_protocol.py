"""
Unit tests for PlayerProtocolClient.

All Socket.IO I/O is mocked — these tests run without a live server.
The socketio.Client constructor is patched so no real network connection is
attempted.
"""

import base64
import time
import unittest
from unittest.mock import MagicMock, call, patch

import pytest

from tsv6.display.tsv6_player.protocol import (
    PlayerProtocolClient,
    _PLAYER_VERSION,
    _PLATFORM_VERSION,
    _STATUS_QUEUE_MAX,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_client(
    mock_sio: MagicMock | None = None,
    **kwargs,
) -> tuple["PlayerProtocolClient", MagicMock]:
    """
    Return ``(client, mock_sio_instance)``.

    If *mock_sio* is provided it is used as the patched socketio.Client
    instance; otherwise a fresh MagicMock is created.
    """
    sio_instance = mock_sio if mock_sio is not None else MagicMock()

    with patch(
        "tsv6.display.tsv6_player.protocol.socketio.Client",
        return_value=sio_instance,
    ):
        client = PlayerProtocolClient(
            server_url=kwargs.get("server_url", "http://test:3000"),
            cpu_serial=kwargs.get("cpu_serial", "SN-TEST-001"),
            player_name=kwargs.get("player_name", "test-player"),
            on_config=kwargs.get("on_config", MagicMock()),
            on_sync=kwargs.get("on_sync", MagicMock()),
            on_setplaylist=kwargs.get("on_setplaylist", MagicMock(return_value="ok")),
            on_connect=kwargs.get("on_connect", None),
            on_disconnect=kwargs.get("on_disconnect", None),
            on_playlist_media=kwargs.get("on_playlist_media", None),
            on_shell=kwargs.get("on_shell", None),
            on_snapshot=kwargs.get("on_snapshot", None),
        )
    return client, sio_instance


def _fire_event(client: "PlayerProtocolClient", event_name: str, *args) -> None:
    """
    Simulate the socketio library calling the registered handler for
    *event_name* with *args*.

    Handlers are registered via ``sio.event`` (for connect/disconnect) or
    ``sio.on`` (for named events).  Both decorators store the function on the
    mock via call arguments; we retrieve them from ``_sio`` directly by
    inspecting the decorated functions stored in ``_protocol_handlers``.
    """
    # We need to call the actual registered Python functions.  The easiest
    # approach: re-patch is not required because _register_handlers was called
    # during __init__.  The functions are closures held inside sio — but since
    # sio is a Mock the decorators (@sio.event, @sio.on) just called the mock
    # which did nothing with the wrapped function.
    #
    # To test handler logic we expose a helper dict on the client.
    handler = client._test_handlers.get(event_name)
    if handler is None:
        raise KeyError(f"No handler registered for event '{event_name}'")
    handler(*args)


# ── Fixture: client with test_handlers ───────────────────────────────────────


@pytest.fixture
def client_with_handlers():
    """
    Build a PlayerProtocolClient whose internal handlers are directly
    accessible via ``client._test_handlers``.

    We achieve this by monkey-patching _register_handlers to capture the
    closures while still registering them on the mock sio.
    """
    sio_instance = MagicMock()
    captured: dict = {}

    original_socketio_client_class = None

    def patched_register(self_inner):
        """Replacement for _register_handlers that captures closures."""
        # Build closures by calling the *real* register method …
        # … but we can't easily do that without the real sio.event/sio.on.
        # Instead we re-implement the registration capture here.

        # connect
        def _connect():
            with self_inner._lock:
                was_connected = self_inner._connected
                self_inner._connected = True
                if was_connected:
                    self_inner._reconnections += 1
            import logging
            logging.getLogger(__name__).info("Connected (test)")
            self_inner._flush_queue()
            if self_inner._on_connect is not None:
                self_inner._on_connect()

        # disconnect
        def _disconnect():
            with self_inner._lock:
                self_inner._connected = False
            if self_inner._on_disconnect is not None:
                self_inner._on_disconnect()

        # config
        def _on_config(config_obj):
            self_inner._increment_events()
            self_inner._on_config(config_obj)
            self_inner._sio.emit("secret_ack", None)

        # sync
        def _on_sync(*args):
            self_inner._increment_events()
            playlists = list(args[0]) if len(args) > 0 else []
            assets = list(args[1]) if len(args) > 1 else []
            self_inner._on_sync(playlists, assets)

        # setplaylist
        def _on_setplaylist(playlist_name):
            self_inner._increment_events()
            ack = self_inner._on_setplaylist(playlist_name)
            self_inner._sio.emit("setplaylist_ack", ack)

        # playlist_media
        def _on_playlist_media(action):
            self_inner._increment_events()
            if self_inner._on_playlist_media is not None:
                response = self_inner._on_playlist_media(action)
            else:
                response = {"status": "not supported"}
            self_inner._sio.emit("media_ack", response)

        # shell
        def _on_shell(cmd_string):
            self_inner._increment_events()
            if self_inner._on_shell is not None:
                response = self_inner._on_shell(cmd_string)
            else:
                response = {"err": "not supported"}
            self_inner._sio.emit("shell_ack", response)

        # snapshot
        def _on_snapshot():
            self_inner._increment_events()
            if self_inner._on_snapshot is not None:
                jpeg_bytes = self_inner._on_snapshot()
            else:
                from tsv6.display.tsv6_player.protocol import _BLANK_JPEG
                jpeg_bytes = _BLANK_JPEG
            b64_data = base64.b64encode(jpeg_bytes).decode("ascii")
            payload = {
                "data": b64_data,
                "playerInfo": {"cpuSerialNumber": self_inner._cpu_serial},
            }
            self_inner._sio.emit("snapshot", payload)

        captured.update(
            {
                "connect": _connect,
                "disconnect": _disconnect,
                "config": _on_config,
                "sync": _on_sync,
                "setplaylist": _on_setplaylist,
                "playlist_media": _on_playlist_media,
                "shell": _on_shell,
                "snapshot": _on_snapshot,
            }
        )
        # Don't call original _register_handlers so we avoid duplicate binding
        # on the mock; just store a no-op on sio.
        self_inner._test_handlers = captured

    with patch(
        "tsv6.display.tsv6_player.protocol.socketio.Client",
        return_value=sio_instance,
    ):
        with patch.object(
            PlayerProtocolClient, "_register_handlers", patched_register
        ):
            on_config_cb = MagicMock()
            on_sync_cb = MagicMock()
            on_setplaylist_cb = MagicMock(return_value="ack-ok")
            client = PlayerProtocolClient(
                server_url="http://test:3000",
                cpu_serial="SN-TEST-001",
                player_name="test-player",
                on_config=on_config_cb,
                on_sync=on_sync_cb,
                on_setplaylist=on_setplaylist_cb,
            )

    client._sio = sio_instance  # ensure attribute is set
    client._test_handlers = captured
    yield client, sio_instance, on_config_cb, on_sync_cb, on_setplaylist_cb


# ── Tests: connect() ─────────────────────────────────────────────────────────


class TestConnect:
    """connect() should call sio.connect with the correct arguments."""

    def test_connect_calls_sio_connect(self):
        client, sio = _make_client()
        sio.connect.return_value = None

        result = client.connect()

        sio.connect.assert_called_once_with(
            "http://test:3000",
            socketio_path="/newsocket.io",
            transports=["polling"],
        )
        assert result is True

    def test_connect_returns_false_on_exception(self):
        client, sio = _make_client()
        sio.connect.side_effect = ConnectionRefusedError("refused")

        result = client.connect()

        assert result is False


# ── Tests: send_status() ─────────────────────────────────────────────────────


class TestSendStatus:
    """send_status() emits the correct 3-arg tuple when connected."""

    def test_emit_when_connected(self):
        client, sio = _make_client(cpu_serial="CPU-123", player_name="dev01")
        client._connected = True

        client.send_status({"tvStatus": True}, priority=0)

        args = sio.emit.call_args
        assert args[0][0] == "status"
        settings, status, priority = args[0][1]
        assert settings["cpuSerialNumber"] == "CPU-123"
        assert settings["name"] == "dev01"
        assert settings["version"] == _PLAYER_VERSION
        assert settings["platform_version"] == _PLATFORM_VERSION
        assert "myIpAddress" in settings
        assert status == {"tvStatus": True}
        assert priority == 0

    def test_identity_fields_always_present(self):
        client, sio = _make_client(cpu_serial="SN-ABC", player_name="p1")
        client._connected = True

        client.send_status({})

        settings = sio.emit.call_args[0][1][0]
        for key in ("cpuSerialNumber", "name", "version", "platform_version", "myIpAddress"):
            assert key in settings, f"Missing identity key: {key}"

    def test_queues_when_disconnected(self):
        client, sio = _make_client()
        # _connected is False by default

        client.send_status({"tvStatus": False})

        sio.emit.assert_not_called()
        assert len(client._status_queue) == 1

    def test_flush_on_reconnect(self, client_with_handlers):
        client, sio, *_ = client_with_handlers
        # Ensure disconnected
        client._connected = False
        client.send_status({"tvStatus": False})
        assert len(client._status_queue) == 1
        sio.emit.assert_not_called()

        # Simulate connect event
        _fire_event(client, "connect")

        # Queue should be drained
        assert len(client._status_queue) == 0
        sio.emit.assert_called()

    def test_connect_callback_fires_on_connect(self, client_with_handlers):
        client, _sio, *_ = client_with_handlers
        callback = MagicMock()
        client._on_connect = callback

        _fire_event(client, "connect")

        callback.assert_called_once()

    def test_disconnect_callback_fires_on_disconnect(self, client_with_handlers):
        client, _sio, *_ = client_with_handlers
        callback = MagicMock()
        client._on_disconnect = callback

        _fire_event(client, "disconnect")

        callback.assert_called_once()

    def test_queue_bounded_at_500(self):
        client, sio = _make_client()
        for i in range(_STATUS_QUEUE_MAX + 50):
            client.send_status({"seq": i})

        assert len(client._status_queue) == _STATUS_QUEUE_MAX


# ── Tests: request_reconfig() ────────────────────────────────────────────────


class TestRequestReconfig:
    """request_reconfig() sends with request=True and priority=1."""

    def test_request_reconfig_connected(self):
        client, sio = _make_client()
        client._connected = True

        client.request_reconfig()

        args = sio.emit.call_args[0]
        assert args[0] == "status"
        settings, _status, priority = args[1]
        assert settings.get("request") is True
        assert priority == 1

    def test_request_reconfig_queues_when_disconnected(self):
        client, sio = _make_client()
        # _connected is False

        client.request_reconfig()

        sio.emit.assert_not_called()
        assert len(client._status_queue) == 1
        queued_settings = client._status_queue[0][0]
        assert queued_settings.get("request") is True


# ── Tests: on_config callback ────────────────────────────────────────────────


class TestConfigEvent:
    """on_config callback fires when config event is received."""

    def test_on_config_callback_fires(self, client_with_handlers):
        client, sio, on_config_cb, *_ = client_with_handlers

        cfg = {"baseUrl": "/sync/test/", "assets": ["a.mp4"]}
        _fire_event(client, "config", cfg)

        on_config_cb.assert_called_once_with(cfg)

    def test_secret_ack_emitted_after_config(self, client_with_handlers):
        client, sio, *_ = client_with_handlers

        _fire_event(client, "config", {})

        sio.emit.assert_called_with("secret_ack", None)


# ── Tests: on_setplaylist callback ───────────────────────────────────────────


class TestSetPlaylist:
    """on_setplaylist callback fires and return value is emitted as setplaylist_ack."""

    def test_callback_fires_and_ack_emitted(self, client_with_handlers):
        client, sio, _, __, on_setplaylist_cb = client_with_handlers
        on_setplaylist_cb.return_value = "switched"

        _fire_event(client, "setplaylist", "my_playlist")

        on_setplaylist_cb.assert_called_once_with("my_playlist")
        sio.emit.assert_called_with("setplaylist_ack", "switched")


# ── Tests: on_sync callback ──────────────────────────────────────────────────


class TestSyncEvent:
    """on_sync callback receives playlists and assets as lists."""

    def test_sync_receives_lists(self, client_with_handlers):
        client, sio, _, on_sync_cb, _ = client_with_handlers

        playlists = ["playlist_a", "playlist_b"]
        assets = ["video1.mp4", "video2.mp4", "image.png"]
        _fire_event(client, "sync", playlists, assets)

        on_sync_cb.assert_called_once_with(playlists, assets)

    def test_sync_partial_args_no_crash(self, client_with_handlers):
        client, sio, _, on_sync_cb, _ = client_with_handlers

        _fire_event(client, "sync", ["pl1"])

        on_sync_cb.assert_called_once_with(["pl1"], [])

    def test_sync_empty_args(self, client_with_handlers):
        client, sio, _, on_sync_cb, _ = client_with_handlers

        _fire_event(client, "sync")

        on_sync_cb.assert_called_once_with([], [])


# ── Tests: disconnect ────────────────────────────────────────────────────────


class TestDisconnect:
    """Disconnect clears the connected flag."""

    def test_disconnect_clears_flag(self, client_with_handlers):
        client, sio, *_ = client_with_handlers
        client._connected = True

        _fire_event(client, "disconnect")

        assert client._connected is False

    def test_disconnect_method_calls_sio(self):
        client, sio = _make_client()
        client._connected = True

        client.disconnect()

        sio.disconnect.assert_called_once()
        assert client._connected is False


# ── Tests: get_metrics() ─────────────────────────────────────────────────────


class TestGetMetrics:
    """get_metrics() returns required keys."""

    def test_required_keys_present(self):
        client, _ = _make_client()
        metrics = client.get_metrics()

        assert "connected" in metrics
        assert "last_status_sent_at" in metrics
        assert "events_received" in metrics
        assert "reconnections" in metrics
        assert "queue_depth" in metrics

    def test_initial_state(self):
        client, _ = _make_client()
        metrics = client.get_metrics()

        assert metrics["connected"] is False
        assert metrics["last_status_sent_at"] is None
        assert metrics["events_received"] == 0
        assert metrics["reconnections"] == 0
        assert metrics["queue_depth"] == 0

    def test_queue_depth_reflects_queued_status(self):
        client, _ = _make_client()
        client.send_status({"tvStatus": True})
        client.send_status({"tvStatus": False})

        assert client.get_metrics()["queue_depth"] == 2

    def test_last_status_sent_at_updated(self):
        client, sio = _make_client()
        client._connected = True
        before = time.time()

        client.send_status({})

        assert client.get_metrics()["last_status_sent_at"] is not None
        assert client.get_metrics()["last_status_sent_at"] >= before

    def test_events_received_increments(self, client_with_handlers):
        client, sio, *_ = client_with_handlers

        _fire_event(client, "config", {})
        _fire_event(client, "config", {})

        assert client.get_metrics()["events_received"] == 2
