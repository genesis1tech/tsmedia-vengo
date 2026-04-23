"""
Unit tests for the TSV6 impression recording layer.

Covers:
- JSONLImpressionRecorder: JSONL output, non-blocking record(), buffer overflow/drop,
  date-partitioned filenames, size-based rotation, retention enforcement, flush(),
  graceful stop() (no data loss), thread-safety.
- ImpressionTracker: system playlist filtering, idle-loop recording, completion-rate
  computation, completed flag, on_play_interrupted(), out-of-order events, creative_map.
- Round-trip: a JSONL line parses back into a reconstructed ImpressionEvent.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tsv6.display.tsv6_player.impression_builder import ImpressionTracker
from tsv6.display.tsv6_player.impressions import ImpressionEvent, JSONLImpressionRecorder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(**overrides: Any) -> ImpressionEvent:
    """Return a minimal valid ImpressionEvent, with optional field overrides."""
    now = datetime.now(timezone.utc).isoformat()
    defaults: dict[str, Any] = dict(
        impression_id=str(uuid.uuid4()),
        player_id="TS_TEST0001",
        venue_id="venue-test",
        installation_id="g1test01",
        asset_id="test_ad.mp4",
        asset_type="video",
        creative_id=None,
        campaign_id=None,
        playlist_name="tsv6_idle_loop",
        timestamp_start=now,
        timestamp_end=now,
        duration_planned_ms=30000,
        duration_actual_ms=30000,
        completion_rate=1.0,
        completed=True,
        playback_context={"hour_of_day": 14, "adjacent_before": None, "adjacent_after": None},
        app_version="6.0.1",
    )
    defaults.update(overrides)
    return ImpressionEvent(**defaults)


def _wait_for(condition_fn, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll condition_fn until True or timeout expires.  Returns True if met."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition_fn():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# JSONLImpressionRecorder tests
# ---------------------------------------------------------------------------


class TestJSONLImpressionRecorderWrite:
    """Tests around basic write and JSONL format."""

    def test_writes_valid_jsonl(self, tmp_path: Path) -> None:
        """Each recorded event produces a parseable JSON line."""
        recorder = JSONLImpressionRecorder(output_dir=tmp_path, flush_interval_s=0.1)
        recorder.start()
        event = _make_event()
        recorder.record(event)
        recorder.stop()

        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1, "Expected exactly one JSONL file"
        lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["impression_id"] == event.impression_id

    def test_multiple_events_all_lines_parseable(self, tmp_path: Path) -> None:
        """Multiple events are each written on their own line."""
        recorder = JSONLImpressionRecorder(output_dir=tmp_path, flush_interval_s=0.1)
        recorder.start()
        events = [_make_event() for _ in range(10)]
        for e in events:
            recorder.record(e)
        recorder.stop()

        lines = (list(tmp_path.glob("*.jsonl"))[0]).read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 10
        ids = {json.loads(line)["impression_id"] for line in lines}
        assert ids == {e.impression_id for e in events}

    def test_round_trip(self, tmp_path: Path) -> None:
        """A written JSONL line can be reconstructed into an ImpressionEvent."""
        recorder = JSONLImpressionRecorder(output_dir=tmp_path, flush_interval_s=0.1)
        recorder.start()
        original = _make_event()
        recorder.record(original)
        recorder.stop()

        line = list(tmp_path.glob("*.jsonl"))[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        reconstructed = ImpressionEvent(**parsed)
        assert reconstructed == original

    def test_date_partitioned_filename(self, tmp_path: Path) -> None:
        """JSONL file name starts with today's UTC date in YYYY-MM-DD format."""
        recorder = JSONLImpressionRecorder(output_dir=tmp_path, flush_interval_s=0.1)
        recorder.start()
        recorder.record(_make_event())
        recorder.stop()

        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert jsonl_files[0].name.startswith(today)

    def test_output_dir_created_if_missing(self, tmp_path: Path) -> None:
        """start() creates the output directory when it does not yet exist."""
        nested = tmp_path / "deep" / "nested"
        recorder = JSONLImpressionRecorder(output_dir=nested, flush_interval_s=0.1)
        recorder.start()
        recorder.stop()
        assert nested.is_dir()


class TestJSONLImpressionRecorderNonBlocking:
    """Non-blocking and buffer-overflow behaviour."""

    def test_record_is_non_blocking_when_buffer_full(self, tmp_path: Path) -> None:
        """record() returns quickly even when the internal buffer is at capacity."""
        # Use a tiny buffer and don't start the writer — queue fills immediately.
        recorder = JSONLImpressionRecorder(output_dir=tmp_path, max_buffer_size=5)
        # Do NOT call recorder.start() — writer thread is not running.

        start = time.monotonic()
        for _ in range(20):
            recorder.record(_make_event())
        elapsed = time.monotonic() - start

        # 20 record() calls on a size-5 buffer without a writer thread should
        # complete in well under 1 second since they must never block on I/O.
        assert elapsed < 1.0, f"record() blocked for {elapsed:.3f}s"

    def test_events_dropped_increments_on_overflow(self, tmp_path: Path) -> None:
        """events_dropped increases when the queue is full and records are dropped."""
        recorder = JSONLImpressionRecorder(output_dir=tmp_path, max_buffer_size=5)
        # Writer NOT running so queue fills up.
        for _ in range(20):
            recorder.record(_make_event())

        metrics = recorder.get_metrics()
        assert metrics["events_dropped"] > 0, "Expected drops when buffer overflows"

    def test_no_drop_under_normal_load(self, tmp_path: Path) -> None:
        """With a running writer, moderate load produces zero dropped events."""
        recorder = JSONLImpressionRecorder(
            output_dir=tmp_path,
            max_buffer_size=500,
            flush_interval_s=0.05,
        )
        recorder.start()
        for _ in range(100):
            recorder.record(_make_event())
        recorder.stop()

        metrics = recorder.get_metrics()
        assert metrics["events_dropped"] == 0
        assert metrics["events_written"] == 100


class TestJSONLImpressionRecorderRotation:
    """Size-based file rotation."""

    def test_size_rotation_triggers_new_file(self, tmp_path: Path) -> None:
        """When the active file exceeds max_file_size_mb, writes go to a new file."""
        # Use a 1-byte max to force rotation after every single event.
        recorder = JSONLImpressionRecorder(
            output_dir=tmp_path,
            max_file_size_mb=0,  # effectively 0 bytes threshold
            flush_interval_s=0.05,
        )
        recorder.start()
        for _ in range(3):
            recorder.record(_make_event())
        recorder.stop()

        jsonl_files = list(tmp_path.glob("*.jsonl"))
        # With near-zero size limit, we expect multiple files.
        assert len(jsonl_files) >= 2, f"Expected rotation, got {len(jsonl_files)} files"

    def test_rotation_suffix_format(self, tmp_path: Path) -> None:
        """Rotated files use the YYYY-MM-DD.<n>.jsonl naming convention."""
        recorder = JSONLImpressionRecorder(
            output_dir=tmp_path,
            max_file_size_mb=0,
            flush_interval_s=0.05,
        )
        recorder.start()
        for _ in range(5):
            recorder.record(_make_event())
        recorder.stop()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        jsonl_files = {f.name for f in tmp_path.glob("*.jsonl")}
        # Primary file or at least one rotation suffix should exist.
        assert any(name.startswith(today) for name in jsonl_files)
        # At least one rotation file should have a numeric suffix.
        rotation_files = [n for n in jsonl_files if f"{today}." in n and not n.endswith(f"{today}.jsonl")]
        assert len(rotation_files) >= 1, f"No rotation files found in {jsonl_files}"


class TestJSONLImpressionRecorderRetention:
    """Retention enforcement."""

    def test_old_files_deleted_on_start(self, tmp_path: Path) -> None:
        """Files older than retention_days are deleted when start() is called."""
        # Create a stale file with an old mtime.
        stale = tmp_path / "2020-01-01.jsonl"
        stale.write_text("{}\n", encoding="utf-8")
        old_mtime = time.time() - (91 * 86400)  # 91 days ago
        os.utime(stale, (old_mtime, old_mtime))

        recorder = JSONLImpressionRecorder(
            output_dir=tmp_path,
            retention_days=90,
            flush_interval_s=0.1,
        )
        recorder.start()
        recorder.stop()

        assert not stale.exists(), "Stale file should have been deleted by retention enforcement"

    def test_recent_files_not_deleted(self, tmp_path: Path) -> None:
        """Files within retention_days are preserved."""
        recent = tmp_path / "2099-12-31.jsonl"
        recent.write_text("{}\n", encoding="utf-8")

        recorder = JSONLImpressionRecorder(
            output_dir=tmp_path,
            retention_days=90,
            flush_interval_s=0.1,
        )
        recorder.start()
        recorder.stop()

        assert recent.exists(), "Recent file should not be deleted"


import os  # noqa: E402  (needed by test above)


class TestJSONLImpressionRecorderFlushStop:
    """flush() and stop() guarantees."""

    def test_flush_forces_immediate_write(self, tmp_path: Path) -> None:
        """flush() ensures buffered events are on disk before it returns."""
        recorder = JSONLImpressionRecorder(
            output_dir=tmp_path,
            flush_interval_s=60.0,  # very long interval so auto-flush won't fire
        )
        recorder.start()
        recorder.record(_make_event())
        recorder.flush()

        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_stop_drains_buffer(self, tmp_path: Path) -> None:
        """stop() writes all queued events before returning (no data loss)."""
        recorder = JSONLImpressionRecorder(
            output_dir=tmp_path,
            max_buffer_size=500,
            flush_interval_s=60.0,
        )
        recorder.start()
        n = 200
        for _ in range(n):
            recorder.record(_make_event())
        recorder.stop()

        all_lines: list[str] = []
        for f in tmp_path.glob("*.jsonl"):
            all_lines.extend(f.read_text(encoding="utf-8").strip().splitlines())
        assert len(all_lines) == n, f"Expected {n} events, got {len(all_lines)}"


class TestJSONLImpressionRecorderMetrics:
    """get_metrics() contract."""

    def test_metrics_keys_present(self, tmp_path: Path) -> None:
        """get_metrics() returns all required keys."""
        recorder = JSONLImpressionRecorder(output_dir=tmp_path, flush_interval_s=0.1)
        recorder.start()
        recorder.record(_make_event())
        recorder.stop()

        metrics = recorder.get_metrics()
        required = {
            "events_buffered",
            "events_written",
            "events_dropped",
            "current_file",
            "total_files",
            "total_bytes_on_disk",
            "oldest_file_date",
        }
        assert required.issubset(metrics.keys())

    def test_metrics_counts_accurate(self, tmp_path: Path) -> None:
        """events_written matches number of events recorded after stop()."""
        recorder = JSONLImpressionRecorder(output_dir=tmp_path, flush_interval_s=0.1)
        recorder.start()
        n = 42
        for _ in range(n):
            recorder.record(_make_event())
        recorder.stop()

        metrics = recorder.get_metrics()
        assert metrics["events_written"] == n


class TestJSONLImpressionRecorderThreadSafety:
    """Thread-safety: multiple producers, single writer."""

    def test_ten_producer_threads_all_events_written(self, tmp_path: Path) -> None:
        """10 threads each writing 1000 events produces 10,000 lines on disk."""
        n_threads = 10
        events_per_thread = 1000
        recorder = JSONLImpressionRecorder(
            output_dir=tmp_path,
            max_buffer_size=n_threads * events_per_thread + 100,
            flush_interval_s=0.05,
        )
        recorder.start()

        barrier = threading.Barrier(n_threads)

        def producer() -> None:
            barrier.wait()  # all threads start writing simultaneously
            for _ in range(events_per_thread):
                recorder.record(_make_event())

        threads = [threading.Thread(target=producer) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        recorder.stop()

        all_lines: list[str] = []
        for f in sorted(tmp_path.glob("*.jsonl")):
            all_lines.extend(f.read_text(encoding="utf-8").strip().splitlines())

        expected = n_threads * events_per_thread
        assert len(all_lines) == expected, (
            f"Expected {expected} events, got {len(all_lines)}.  "
            f"Dropped: {recorder.get_metrics()['events_dropped']}"
        )

    def test_all_lines_valid_json(self, tmp_path: Path) -> None:
        """Every line written by the concurrent test is valid JSON."""
        recorder = JSONLImpressionRecorder(
            output_dir=tmp_path,
            max_buffer_size=5100,
            flush_interval_s=0.05,
        )
        recorder.start()

        def producer() -> None:
            for _ in range(50):
                recorder.record(_make_event())

        threads = [threading.Thread(target=producer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        recorder.stop()

        for f in tmp_path.glob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").strip().splitlines():
                json.loads(line)  # raises if invalid


# ---------------------------------------------------------------------------
# ImpressionTracker tests
# ---------------------------------------------------------------------------


class TestImpressionTrackerFiltering:
    """System playlist filtering."""

    @pytest.mark.parametrize(
        "playlist_name",
        [
            "tsv6_processing",
            "tsv6_deposit_item",
            "tsv6_product_display",
            "tsv6_no_match",
            "tsv6_barcode_not_qr",
            "tsv6_no_item_detected",
            "tsv6_offline",
        ],
    )
    def test_system_playlists_filtered(self, playlist_name: str) -> None:
        """on_play_start returns None for every system playlist."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        result = tracker.on_play_start("ad.mp4", playlist_name, duration_planned_ms=10000)
        assert result is None
        recorder.record.assert_not_called()

    def test_system_playlist_end_does_not_record(self) -> None:
        """on_play_end after a filtered start produces no impression."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        tracker.on_play_start("x.mp4", "tsv6_processing", duration_planned_ms=5000)
        tracker.on_play_end("x.mp4")
        recorder.record.assert_not_called()


class TestImpressionTrackerRecording:
    """Impression recording for ad playlist."""

    def test_idle_loop_produces_impression(self) -> None:
        """on_play_start for tsv6_idle_loop returns an impression_id."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        imp_id = tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
        assert imp_id is not None
        # Validate it's a UUID4 string.
        parsed = uuid.UUID(imp_id, version=4)
        assert str(parsed) == imp_id

    def test_record_called_on_play_end(self) -> None:
        """recorder.record() is called exactly once when play ends normally."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
        tracker.on_play_end("ad.mp4")
        recorder.record.assert_called_once()
        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert isinstance(event, ImpressionEvent)
        assert event.asset_id == "ad.mp4"
        assert event.playlist_name == "tsv6_idle_loop"

    def test_impression_id_matches_start_return(self) -> None:
        """The impression_id in the recorded event matches on_play_start's return value."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        imp_id = tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
        tracker.on_play_end("ad.mp4")
        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.impression_id == imp_id


class TestImpressionTrackerCompletionRate:
    """Completion rate and completed flag computation."""

    def test_full_play_completion_rate_one(self) -> None:
        """A play running for exactly the planned duration has completion_rate=1.0."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        import tsv6.display.tsv6_player.impression_builder as ib

        planned_ms = 30000
        with patch.object(ib, "_monotonic", side_effect=[0.0, 30.0]):
            tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=planned_ms)
            tracker.on_play_end("ad.mp4")

        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.completion_rate == pytest.approx(1.0)
        assert event.completed is True

    def test_partial_play_completion_rate_fractional(self) -> None:
        """A play cut to half the planned duration has completion_rate~0.5."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        import tsv6.display.tsv6_player.impression_builder as ib

        with patch.object(ib, "_monotonic", side_effect=[0.0, 15.0]):
            tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
            tracker.on_play_end("ad.mp4")

        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.completion_rate == pytest.approx(0.5, abs=0.01)
        assert event.completed is False

    def test_completion_rate_clamped_to_one(self) -> None:
        """Actual duration exceeding planned duration clamps completion_rate to 1.0."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        import tsv6.display.tsv6_player.impression_builder as ib

        with patch.object(ib, "_monotonic", side_effect=[0.0, 60.0]):  # ran 2x planned
            tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
            tracker.on_play_end("ad.mp4")

        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.completion_rate == pytest.approx(1.0)

    def test_completed_flag_at_95_percent(self) -> None:
        """completed=True when completion_rate >= 0.95."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        import tsv6.display.tsv6_player.impression_builder as ib

        # 28.5 seconds of 30 second ad = 0.95.
        with patch.object(ib, "_monotonic", side_effect=[0.0, 28.5]):
            tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
            tracker.on_play_end("ad.mp4")

        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.completion_rate == pytest.approx(0.95)
        assert event.completed is True

    def test_completed_flag_below_95_percent(self) -> None:
        """completed=False when completion_rate < 0.95."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        import tsv6.display.tsv6_player.impression_builder as ib

        with patch.object(ib, "_monotonic", side_effect=[0.0, 28.0]):  # 93.3%
            tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
            tracker.on_play_end("ad.mp4")

        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.completed is False


class TestImpressionTrackerInterrupted:
    """on_play_interrupted() behaviour."""

    def test_interrupted_sets_completed_false(self) -> None:
        """on_play_interrupted records completed=False regardless of elapsed time."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        import tsv6.display.tsv6_player.impression_builder as ib

        # Even though the full 30 seconds elapsed, interrupted=True forces completed=False.
        with patch.object(ib, "_monotonic", side_effect=[0.0, 30.0]):
            tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
            tracker.on_play_interrupted("ad.mp4")

        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.completed is False

    def test_interrupted_still_records_completion_rate(self) -> None:
        """on_play_interrupted still records the actual completion_rate fraction."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        import tsv6.display.tsv6_player.impression_builder as ib

        with patch.object(ib, "_monotonic", side_effect=[0.0, 15.0]):
            tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
            tracker.on_play_interrupted("ad.mp4")

        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.completion_rate == pytest.approx(0.5, abs=0.01)


class TestImpressionTrackerEdgeCases:
    """Out-of-order and edge-case handling."""

    def test_end_without_start_no_crash(self) -> None:
        """on_play_end for an unknown asset logs a warning but does not raise."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        # Should not raise.
        tracker.on_play_end("nonexistent.mp4")
        recorder.record.assert_not_called()

    def test_interrupted_without_start_no_crash(self) -> None:
        """on_play_interrupted for an unknown asset does not raise."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        tracker.on_play_interrupted("ghost.mp4")
        recorder.record.assert_not_called()

    def test_duplicate_start_orphans_previous(self) -> None:
        """A second on_play_start for same asset before end orphans the first impression."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        first_id = tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
        second_id = tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
        assert first_id != second_id
        tracker.on_play_end("ad.mp4")
        # Only one impression is recorded (for the second start).
        recorder.record.assert_called_once()
        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.impression_id == second_id

    def test_get_in_flight_returns_correct_state(self) -> None:
        """get_in_flight() returns currently tracked plays."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
        )
        imp_id = tracker.on_play_start("ad.mp4", "tsv6_idle_loop", duration_planned_ms=10000)
        in_flight = tracker.get_in_flight()
        assert "ad.mp4" in in_flight
        assert in_flight["ad.mp4"]["impression_id"] == imp_id
        tracker.on_play_end("ad.mp4")
        assert tracker.get_in_flight() == {}


class TestImpressionTrackerCreativeMap:
    """creative_map lookup behaviour."""

    def test_creative_map_attaches_ids(self) -> None:
        """creative_id and campaign_id are populated from creative_map when asset matches."""
        recorder = MagicMock()
        creative_map = {
            "pepsi_30s.mp4": {"creative_id": "CR-001", "campaign_id": "CAMP-42"},
        }
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
            creative_map=creative_map,
        )
        tracker.on_play_start("pepsi_30s.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
        tracker.on_play_end("pepsi_30s.mp4")

        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.creative_id == "CR-001"
        assert event.campaign_id == "CAMP-42"

    def test_creative_map_none_for_unmapped_asset(self) -> None:
        """creative_id and campaign_id are None when asset not in creative_map."""
        recorder = MagicMock()
        tracker = ImpressionTracker(
            recorder=recorder,
            player_id="TS_TEST",
            installation_id="g1test",
            app_version="6.0.1",
            creative_map={"other.mp4": {"creative_id": "X", "campaign_id": "Y"}},
        )
        tracker.on_play_start("unmapped.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
        tracker.on_play_end("unmapped.mp4")

        event: ImpressionEvent = recorder.record.call_args[0][0]
        assert event.creative_id is None
        assert event.campaign_id is None


# ---------------------------------------------------------------------------
# Round-trip sample output (printed to stdout for human verification)
# ---------------------------------------------------------------------------


def test_sample_jsonl_line_to_stdout(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Write one event, print the raw JSONL line, and verify round-trip parse."""
    recorder_impl = JSONLImpressionRecorder(output_dir=tmp_path, flush_interval_s=0.1)
    recorder_impl.start()

    event = _make_event(
        impression_id="00000000-0000-4000-a000-000000000001",
        asset_id="sample_ad.mp4",
        creative_id="CR-DEMO",
        campaign_id="CAMP-DEMO",
        completion_rate=1.0,
        completed=True,
    )
    recorder_impl.record(event)
    recorder_impl.stop()

    jsonl_files = list(tmp_path.glob("*.jsonl"))
    line = jsonl_files[0].read_text(encoding="utf-8").strip()

    print("\n--- Sample JSONL output ---")
    print(line)
    print("--- End sample ---\n")

    parsed = json.loads(line)
    reconstructed = ImpressionEvent(**parsed)
    assert reconstructed == event, "Round-trip reconstruction failed"
