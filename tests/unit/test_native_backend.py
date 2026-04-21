"""
Unit tests for TSV6NativeBackend.

All four subsystems are mocked — these tests never touch the filesystem,
make network connections, or start real threads (except the status thread
which is stopped immediately in each teardown).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from tsv6.display.identity import PlayerIdentity
from tsv6.display.tsv6_player.backend import TSV6NativeBackend, _IDLE_PLAYLIST


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_identity() -> PlayerIdentity:
    return PlayerIdentity(
        cpu_serial="000000001234ABCD",
        device_id="1234ABCD",
        player_name="TS_1234ABCD",
        eth_mac="aa:bb:cc:dd:ee:ff",
        wlan_mac="11:22:33:44:55:66",
    )


def _make_backend(
    tmp_path: Path,
    server_url: str = "http://test:3000",
) -> TSV6NativeBackend:
    """Return a backend with all subsystems pre-stubbed via identity_override."""
    return TSV6NativeBackend(
        server_url=server_url,
        username="testuser",
        password="testpass",
        cache_dir=tmp_path / "cache",
        layout_html=tmp_path / "layout.html",
        installation="testinstall",
        group_name="testgroup",
        app_version="0.0.1",
        venue_id="venue-test",
        impression_output_dir=tmp_path / "impressions",
        identity_override=_make_identity(),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_backend(tmp_path: Path):
    """Backend instance; subsystems injected via patches."""
    return _make_backend(tmp_path)


@pytest.fixture
def mock_protocol():
    m = MagicMock()
    m.connect.return_value = True
    m.is_connected.return_value = True
    m.get_metrics.return_value = {
        "connected": True,
        "events_received": 0,
        "reconnections": 0,
        "queue_depth": 0,
        "last_status_sent_at": None,
    }
    return m


@pytest.fixture
def mock_syncer():
    m = MagicMock()
    sync_result = MagicMock()
    sync_result.updated = 1
    sync_result.unchanged = 0
    sync_result.failed = 0
    m.sync.return_value = sync_result
    m.get_metrics.return_value = {
        "total_files_cached": 1,
        "total_bytes_cached": 1024,
        "last_sync_at": "2026-04-20T00:00:00+00:00",
        "failed_syncs": 0,
    }
    return m


@pytest.fixture
def mock_renderer():
    m = MagicMock()
    m.start.return_value = True
    m.is_connected = True
    m.show_idle.return_value = True
    m.show_processing.return_value = True
    m.show_deposit_item.return_value = True
    m.show_product_display.return_value = True
    m.show_no_match.return_value = True
    m.show_barcode_not_qr.return_value = True
    m.show_no_item_detected.return_value = True
    m.show_offline.return_value = True
    m.get_metrics.return_value = {
        "state": "idle",
        "chromium_running": True,
        "vlc_playing": False,
        "main_rect": (0, 0, 800, 420),
        "router_url": "http://127.0.0.1:8765",
    }
    return m


@pytest.fixture
def mock_recorder():
    m = MagicMock()
    m.get_metrics.return_value = {
        "events_buffered": 0,
        "events_written": 0,
        "events_dropped": 0,
        "current_file": None,
        "total_files": 0,
        "total_bytes_on_disk": 0,
        "oldest_file_date": None,
    }
    return m


@pytest.fixture
def mock_tracker():
    m = MagicMock()
    m.on_play_start.return_value = "test-impression-id"
    return m


def _connect_backend(
    backend: TSV6NativeBackend,
    mock_protocol: MagicMock,
    mock_syncer: MagicMock,
    mock_renderer: MagicMock,
    mock_recorder: MagicMock,
    mock_tracker: MagicMock,
) -> bool:
    """
    Patch all four subsystem constructors so connect() uses the mocks,
    then call connect().  Returns True if connect() succeeded.
    """
    # _import_renderer() returns the class; we need to return a mock CLASS
    # whose call (i.e. __call__) returns mock_renderer.
    renderer_class_mock = MagicMock(return_value=mock_renderer)

    with (
        patch(
            "tsv6.display.tsv6_player.backend.PlayerProtocolClient",
            return_value=mock_protocol,
        ),
        patch(
            "tsv6.display.tsv6_player.backend.AssetSyncer",
            return_value=mock_syncer,
        ),
        patch(
            "tsv6.display.tsv6_player.backend._import_renderer",
            return_value=renderer_class_mock,
        ),
        patch(
            "tsv6.display.tsv6_player.backend.JSONLImpressionRecorder",
            return_value=mock_recorder,
        ),
        patch(
            "tsv6.display.tsv6_player.backend.ImpressionTracker",
            return_value=mock_tracker,
        ),
    ):
        result = backend.connect()

    # After connect, assign the mocks directly so the backend uses them for
    # subsequent calls.
    backend._protocol = mock_protocol
    backend._syncer = mock_syncer
    backend._renderer = mock_renderer
    backend._recorder = mock_recorder
    backend._tracker = mock_tracker
    return result


# ── Tests: connect() ──────────────────────────────────────────────────────────


class TestConnect:
    def test_connect_wires_protocol_and_returns_true(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        result = _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )
        assert result is True

    def test_connect_calls_request_reconfig(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )
        mock_protocol.request_reconfig.assert_called_once()

    def test_connect_returns_false_when_protocol_fails(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        mock_protocol.connect.return_value = False
        result = _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )
        assert result is False

    def test_connect_builds_syncer_with_correct_base_path(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        renderer_class_mock = MagicMock(return_value=mock_renderer)
        with (
            patch(
                "tsv6.display.tsv6_player.backend.PlayerProtocolClient",
                return_value=mock_protocol,
            ),
            patch(
                "tsv6.display.tsv6_player.backend.AssetSyncer",
                return_value=mock_syncer,
            ) as patched_syncer_cls,
            patch(
                "tsv6.display.tsv6_player.backend._import_renderer",
                return_value=renderer_class_mock,
            ),
            patch(
                "tsv6.display.tsv6_player.backend.JSONLImpressionRecorder",
                return_value=mock_recorder,
            ),
            patch(
                "tsv6.display.tsv6_player.backend.ImpressionTracker",
                return_value=mock_tracker,
            ),
        ):
            tmp_backend.connect()

        _, kwargs = patched_syncer_cls.call_args
        assert kwargs.get("base_path") == "/sync_folders/testinstall/testgroup/"


# ── Tests: on_config callback ─────────────────────────────────────────────────


class TestOnConfig:
    def test_on_config_triggers_asset_sync(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        config_obj = {
            "assets": ["video1.mp4", "video2.mp4"],
            "playlists": [],
        }
        tmp_backend._on_config(config_obj)

        mock_syncer.sync.assert_called_once_with(["video1.mp4", "video2.mp4"])

    def test_on_config_caches_playlist_assets(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
        tmp_path,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        config_obj = {
            "assets": [],
            "playlists": [
                {"name": _IDLE_PLAYLIST, "assets": ["ad1.mp4", "ad2.mp4"]},
            ],
        }
        tmp_backend._on_config(config_obj)

        assert tmp_backend._playlist_assets[_IDLE_PLAYLIST] == ["ad1.mp4", "ad2.mp4"]

        # Verify the playlist cache file was written.
        cache_file = tmp_backend._cache_dir / f"__{_IDLE_PLAYLIST}.json"
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data == ["ad1.mp4", "ad2.mp4"]


# ── Tests: on_setplaylist callback ────────────────────────────────────────────


class TestOnSetPlaylist:
    def test_setplaylist_idle_calls_show_idle(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        # Pre-populate the idle playlist cache so show_idle can resolve MP4s.
        tmp_backend._cache_dir.mkdir(parents=True, exist_ok=True)
        (tmp_backend._cache_dir / "ad.mp4").write_bytes(b"fake")
        tmp_backend._write_playlist_cache(_IDLE_PLAYLIST, ["ad.mp4"])

        tmp_backend._on_setplaylist(_IDLE_PLAYLIST)

        mock_renderer.show_idle.assert_called_once()
        # Impression tracking should have started for the ad.
        mock_tracker.on_play_start.assert_called_once()
        call_kwargs = mock_tracker.on_play_start.call_args
        assert call_kwargs.kwargs.get("playlist_name") == _IDLE_PLAYLIST

    def test_setplaylist_processing_calls_show_processing_no_impression(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        tmp_backend._on_setplaylist("tsv6_processing")

        mock_renderer.show_processing.assert_called_once()
        # No impression should be started for system playlists.
        mock_tracker.on_play_start.assert_not_called()

    def test_setplaylist_returns_ack_string(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        ack = tmp_backend._on_setplaylist("tsv6_processing")
        assert isinstance(ack, str)
        assert "tsv6_processing" in ack


# ── Tests: show_product_display ───────────────────────────────────────────────


class TestShowProductDisplay:
    def test_forwards_to_renderer_with_same_args(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        result = tmp_backend.show_product_display(
            product_image_path="/tmp/img.jpg",
            qr_url="https://example.com/qr",
            nfc_url="https://example.com/nfc",
        )

        assert result is True
        mock_renderer.show_product_display.assert_called_once_with(
            image_path=Path("/tmp/img.jpg"),
            qr_url="https://example.com/qr",
            nfc_url="https://example.com/nfc",
        )

    def test_forwards_without_nfc_url(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        tmp_backend.show_product_display(
            product_image_path="/tmp/img.jpg",
            qr_url="https://example.com/qr",
        )

        _, kwargs = mock_renderer.show_product_display.call_args
        assert kwargs.get("nfc_url") is None


# ── Tests: impression interruption on state transition ────────────────────────


class TestImpressionInterruption:
    def test_show_processing_interrupts_in_flight_impression(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
        tmp_path,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        # Simulate an in-flight idle impression.
        tmp_backend._cache_dir.mkdir(parents=True, exist_ok=True)
        (tmp_backend._cache_dir / "ad.mp4").write_bytes(b"fake")
        tmp_backend._write_playlist_cache(_IDLE_PLAYLIST, ["ad.mp4"])
        tmp_backend.show_idle()

        assert tmp_backend._current_idle_asset == "ad.mp4"

        # Transitioning to processing must call on_play_interrupted.
        tmp_backend.show_processing()

        mock_tracker.on_play_interrupted.assert_called_once_with("ad.mp4")
        assert tmp_backend._current_idle_asset is None

    def test_show_no_match_interrupts_in_flight_impression(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
        tmp_path,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        tmp_backend._cache_dir.mkdir(parents=True, exist_ok=True)
        (tmp_backend._cache_dir / "spot.mp4").write_bytes(b"fake")
        tmp_backend._write_playlist_cache(_IDLE_PLAYLIST, ["spot.mp4"])
        tmp_backend.show_idle()

        tmp_backend.show_no_match()

        mock_tracker.on_play_interrupted.assert_called_once_with("spot.mp4")


# ── Tests: get_metrics ────────────────────────────────────────────────────────


class TestGetMetrics:
    def test_merges_all_four_subsystems(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        metrics = tmp_backend.get_metrics()

        assert "protocol_connected" in metrics
        assert "sync_total_files_cached" in metrics
        assert "renderer_state" in metrics
        assert "impression_events_written" in metrics


# ── Tests: show_offline ───────────────────────────────────────────────────────


class TestShowOffline:
    def test_show_offline_calls_renderer(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        mock_protocol.is_connected.return_value = False

        result = tmp_backend.show_offline()

        assert result is True
        mock_renderer.show_offline.assert_called_once()


# ── Tests: stop() ─────────────────────────────────────────────────────────────


class TestStop:
    def test_stop_tears_down_all_subsystems(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )

        # Patch the status thread to avoid real threading in this test.
        tmp_backend._stop_event.set()
        tmp_backend._started = True

        tmp_backend.stop()

        mock_renderer.stop.assert_called_once()
        mock_recorder.stop.assert_called_once()
        mock_protocol.disconnect.assert_called_once()
        assert tmp_backend._started is False

    def test_stop_is_idempotent(
        self,
        tmp_backend,
        mock_protocol,
        mock_syncer,
        mock_renderer,
        mock_recorder,
        mock_tracker,
    ):
        _connect_backend(
            tmp_backend,
            mock_protocol,
            mock_syncer,
            mock_renderer,
            mock_recorder,
            mock_tracker,
        )
        tmp_backend._stop_event.set()
        tmp_backend._started = True

        tmp_backend.stop()
        tmp_backend.stop()  # second call should be safe

        # stop() on each subsystem called at most once.
        assert mock_renderer.stop.call_count <= 1
        assert mock_recorder.stop.call_count <= 1
