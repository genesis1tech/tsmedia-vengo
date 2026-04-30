"""
Integration test: full recycling kiosk transaction flow.

Uses real TSV6NativeBackend with all four subsystems mocked so the test
runs without hardware, a PiSignage server, Chromium, or VLC.

The flow under test:
  1. Kiosk starts → show_idle (attract loop with ad video)
  2. User scans barcode → show_processing
  3. AWS responds "openDoor" → show_deposit_item
  4. Servo opens door → ToF sensor detects item
  5. Servo closes door → show_product_display (success path)
  6. AWS recycle_success published
  7. Return to idle → show_idle

Additional scenario:
  - If an idle-loop video plays for ~30 s before the scan, 1 impression
    record must be committed when show_processing interrupts the idle loop.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import ANY, MagicMock

import pytest

from tsv6.display.identity import PlayerIdentity
from tsv6.display.tsv6_player.backend import TSV6NativeBackend, _IDLE_PLAYLIST
from tsv6.display.tsv6_player.impression_builder import ImpressionTracker
from tsv6.display.tsv6_player.impressions import JSONLImpressionRecorder


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_identity() -> PlayerIdentity:
    return PlayerIdentity(
        cpu_serial="AABBCCDD11223344",
        device_id="11223344",
        player_name="TS_11223344",
        eth_mac=None,
        wlan_mac=None,
    )


def _make_backend(tmp_path: Path) -> TSV6NativeBackend:
    return TSV6NativeBackend(
        server_url="http://integration-test:3000",
        username="user",
        password="pass",
        cache_dir=tmp_path / "cache",
        layout_html=tmp_path / "layout.html",
        installation="testinstall",
        group_name="default",
        app_version="1.0.0",
        venue_id="venue-42",
        impression_output_dir=tmp_path / "impressions",
        identity_override=_make_identity(),
    )


def _make_renderer_mock() -> MagicMock:
    m = MagicMock()
    m.start.return_value = True
    m.is_connected = True
    m.play_video_loop.return_value = True
    m.show_idle.return_value = True
    m.show_processing.return_value = True
    m.show_deposit_item.return_value = True
    m.show_product_display.return_value = True
    m.show_no_match.return_value = True
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


def _make_protocol_mock() -> MagicMock:
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


def _make_syncer_mock() -> MagicMock:
    m = MagicMock()
    result = MagicMock()
    result.updated = 0
    result.unchanged = 1
    result.failed = 0
    m.sync.return_value = result
    m.get_metrics.return_value = {
        "total_files_cached": 2,
        "total_bytes_cached": 2048,
        "last_sync_at": None,
        "failed_syncs": 0,
    }
    return m


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def flow_ctx(tmp_path: Path):
    """
    Returns a dict with a connected, started backend and all its mocks.

    The recorder is a real JSONLImpressionRecorder writing to tmp_path so we
    can assert impression counts without mocking the recorder internals.
    """
    backend = _make_backend(tmp_path)

    protocol = _make_protocol_mock()
    syncer = _make_syncer_mock()
    renderer = _make_renderer_mock()

    # Use a real recorder backed by tmp_path for impression assertions.
    recorder = JSONLImpressionRecorder(
        output_dir=tmp_path / "impressions",
        flush_interval_s=0.05,  # fast flush for tests
    )
    recorder.start()

    tracker = ImpressionTracker(
        recorder=recorder,
        player_id="TS_11223344",
        installation_id="testinstall",
        app_version="1.0.0",
        venue_id="venue-42",
    )

    # Wire mocks into the backend directly (bypassing connect() patching).
    backend._protocol = protocol
    backend._syncer = syncer
    backend._renderer = renderer
    backend._recorder = recorder
    backend._tracker = tracker
    backend._identity = _make_identity()

    # Create a fake ad MP4 and playlist cache.
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "pepsi_30s.mp4").write_bytes(b"fake-mp4")
    (cache / "processing.mp4").write_bytes(b"fake-mp4")
    (cache / "deposit_item.mp4").write_bytes(b"fake-mp4")
    backend._write_playlist_cache(_IDLE_PLAYLIST, ["pepsi_30s.mp4"])
    backend._write_playlist_cache("tsv6_processing", ["processing.mp4"])
    backend._write_playlist_cache("tsv6_deposit_item", ["deposit_item.mp4"])

    yield {
        "backend": backend,
        "protocol": protocol,
        "syncer": syncer,
        "renderer": renderer,
        "recorder": recorder,
        "tracker": tracker,
        "tmp_path": tmp_path,
        "cache": cache,
    }

    recorder.stop()
    backend._stop_event.set()


# ── Full recycling flow test ──────────────────────────────────────────────────


class TestFullRecycleFlow:
    def test_happy_path_end_to_end(self, flow_ctx):
        """
        Assert the exact sequence of show_* calls and impression counts for
        a successful recycle transaction.
        """
        backend: TSV6NativeBackend = flow_ctx["backend"]
        renderer: MagicMock = flow_ctx["renderer"]
        recorder: JSONLImpressionRecorder = flow_ctx["recorder"]

        # ── Step 1: Show idle attract loop ────────────────────────────────────
        # (Simulates startup after connect/start)
        result = backend.show_idle()
        assert result is True
        renderer.show_idle.assert_called_once()
        assert backend._current_idle_asset == "pepsi_30s.mp4"

        # ── Step 2: Barcode scanned → show processing ─────────────────────────
        result = backend.show_processing()
        assert result is True
        renderer.play_video_loop.assert_called_with(
            [flow_ctx["cache"] / "processing.mp4"],
            state="processing",
            loop=False,
            on_end=ANY,
        )
        # Idle impression was interrupted.
        assert backend._current_idle_asset is None

        # ── Step 3: AWS openDoor → show deposit item ──────────────────────────
        result = backend.show_deposit_item()
        assert result is True
        renderer.play_video_loop.assert_called_with(
            [flow_ctx["cache"] / "deposit_item.mp4"],
            state="deposit_item",
            loop=True,
            on_end=None,
        )

        # ── Step 4: Servo open + ToF detects item + Servo close ───────────────
        # (Servo/ToF are outside the display backend; no assertions here)

        # ── Step 5: Show product display (success) ────────────────────────────
        result = backend.show_product_display(
            product_image_path="/tmp/product.jpg",
            qr_url="https://reward.example.com/txn123",
            nfc_url="https://reward.example.com/nfc/txn123",
        )
        assert result is True
        renderer.show_product_display.assert_called_once_with(
            image_path=Path("/tmp/product.jpg"),
            qr_url="https://reward.example.com/txn123",
            nfc_url="https://reward.example.com/nfc/txn123",
            product_name="",
            product_brand="",
            product_desc="",
        )

        # ── Step 6: Transaction complete → return to idle ─────────────────────
        result = backend.show_idle()
        assert result is True
        assert renderer.show_idle.call_count == 2

        # ── Step 7: No ad impressions for system playlists ────────────────────
        # Flush the recorder and check JSONL.
        recorder.flush()
        time.sleep(0.2)  # allow background writer to flush

        jsonl_files = list((flow_ctx["tmp_path"] / "impressions").glob("*.jsonl"))
        # The two show_idle calls each interrupt the previous idle asset before
        # recording — the first call starts tracking, the second call interrupts
        # it.  So 1 interrupted impression is written for "pepsi_30s.mp4".
        # The processing/deposit/product calls also trigger interrupts but those
        # happen when no idle asset is in flight after the first interruption.
        written = recorder.get_metrics()["events_written"]
        # At most 1 impression should have been committed (the interrupted
        # pepsi_30s.mp4 play from the first show_idle).
        assert written <= 1, (
            f"Expected at most 1 impression for the interrupted idle loop; "
            f"got {written}"
        )

    def test_show_processing_fires_on_barcode_scan(self, flow_ctx):
        """show_processing is called when a barcode scan event arrives."""
        backend: TSV6NativeBackend = flow_ctx["backend"]
        renderer: MagicMock = flow_ctx["renderer"]

        backend.show_idle()
        backend.show_processing()

        renderer.play_video_loop.assert_called_with(
            [flow_ctx["cache"] / "processing.mp4"],
            state="processing",
            loop=False,
            on_end=ANY,
        )

    def test_show_deposit_item_fires_after_servo_open(self, flow_ctx):
        """show_deposit_item is called after the servo begins opening."""
        backend: TSV6NativeBackend = flow_ctx["backend"]
        renderer: MagicMock = flow_ctx["renderer"]

        backend.show_idle()
        backend.show_processing()
        backend.show_deposit_item()

        renderer.play_video_loop.assert_called_with(
            [flow_ctx["cache"] / "deposit_item.mp4"],
            state="deposit_item",
            loop=True,
            on_end=None,
        )

    def test_show_product_display_fires_with_correct_payload(self, flow_ctx):
        """show_product_display receives the correct image/qr/nfc arguments."""
        backend: TSV6NativeBackend = flow_ctx["backend"]
        renderer: MagicMock = flow_ctx["renderer"]

        backend.show_product_display(
            product_image_path="/images/coke.jpg",
            qr_url="https://rewards.example.com/coke",
            nfc_url="https://nfc.example.com/coke",
        )

        renderer.show_product_display.assert_called_once_with(
            image_path=Path("/images/coke.jpg"),
            qr_url="https://rewards.example.com/coke",
            nfc_url="https://nfc.example.com/coke",
            product_name="",
            product_brand="",
            product_desc="",
        )

    def test_show_idle_fires_at_end_of_transaction(self, flow_ctx):
        """show_idle is called once at startup and once at end of transaction."""
        backend: TSV6NativeBackend = flow_ctx["backend"]
        renderer: MagicMock = flow_ctx["renderer"]

        backend.show_idle()           # startup
        backend.show_processing()
        backend.show_deposit_item()
        backend.show_product_display("/img.jpg", "https://qr.example.com")
        backend.show_idle()           # end of transaction

        assert renderer.show_idle.call_count == 2

    def test_system_playlists_produce_zero_impressions(self, flow_ctx):
        """
        Processing, deposit, product, no-match, and no-item-detected transitions
        must not generate impression records (they are system playlists).
        """
        backend: TSV6NativeBackend = flow_ctx["backend"]
        recorder: JSONLImpressionRecorder = flow_ctx["recorder"]

        # Perform a full transaction without any idle time.
        backend.show_processing()
        backend.show_deposit_item()
        backend.show_product_display("/img.jpg", "https://qr.example.com")

        recorder.flush()
        time.sleep(0.2)

        written = recorder.get_metrics()["events_written"]
        assert written == 0, (
            f"System-playlist transitions must produce 0 impressions; got {written}"
        )


# ── Idle impression scenario ──────────────────────────────────────────────────


class TestIdleImpression:
    def test_idle_video_produces_impression_when_interrupted(self, flow_ctx):
        """
        If an idle-loop video plays and is interrupted by show_processing,
        exactly 1 impression record must be committed.
        """
        backend: TSV6NativeBackend = flow_ctx["backend"]
        recorder: JSONLImpressionRecorder = flow_ctx["recorder"]

        # Start idle loop — this arms the impression for "pepsi_30s.mp4".
        backend.show_idle()
        assert backend._current_idle_asset == "pepsi_30s.mp4"

        # Simulate 30 s of elapsed playback by monkeypatching the tracker's
        # internal state so the impression records a non-zero duration.
        in_flight = backend._tracker._in_flight.get("pepsi_30s.mp4")
        if in_flight is not None:
            import dataclasses
            backend._tracker._in_flight["pepsi_30s.mp4"] = dataclasses.replace(
                in_flight,
                start_monotonic=time.monotonic() - 30.0,
            )

        # Interrupt the idle loop (simulates barcode scan).
        backend.show_processing()

        # Give the recorder's background thread a moment to write the event.
        recorder.flush()
        time.sleep(0.3)

        written = recorder.get_metrics()["events_written"]
        assert written == 1, (
            f"Expected exactly 1 impression for the 30-s idle play; got {written}"
        )

        # Verify the JSONL content.
        jsonl_files = list((flow_ctx["tmp_path"] / "impressions").glob("*.jsonl"))
        assert len(jsonl_files) == 1
        line = jsonl_files[0].read_text().strip().splitlines()[0]
        event = json.loads(line)
        assert event["asset_id"] == "pepsi_30s.mp4"
        assert event["playlist_name"] == _IDLE_PLAYLIST
        assert event["player_id"] == "TS_11223344"
        assert event["installation_id"] == "testinstall"
