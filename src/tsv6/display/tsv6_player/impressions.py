"""
Impression recording layer for TSV6 ad playback.

Provides a Vistar/OpenRTB-DOOH compatible per-impression event schema and a
non-blocking JSONL writer backed by a background daemon thread.  The design is
forward-compatible with a future Hostinger MongoDB backend: every field maps
trivially to BSON and the ImpressionRecorder Protocol makes the storage layer
swappable without touching callers.

Usage::

    recorder = JSONLImpressionRecorder(output_dir=Path("/home/pi/.local/share/tsv6/impressions"))
    recorder.start()
    # ... player calls recorder.record(event) from any thread ...
    recorder.stop()  # graceful drain + final flush
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ImpressionEvent:
    """Vistar/OpenRTB-DOOH compatible per-impression event.

    One instance is created for each ad asset play (complete or partial).
    Field names are chosen to match OpenRTB-DOOH / Vistar proof-of-play
    conventions so a future MongoDB import is a trivial JSON->BSON mapping.

    All timestamps are ISO 8601 strings with UTC timezone.
    """

    # -- Identity --
    impression_id: str
    """UUID4 uniquely identifying this single play event."""

    player_id: str
    """CPU-serial-derived device identifier (e.g. 'TS_ABCD1234')."""

    venue_id: str | None
    """Operator-assigned venue tag sourced from env TSV6_VENUE_ID."""

    installation_id: str
    """PiSignage installation / group name (e.g. 'g1tech26')."""

    # -- Creative --
    asset_id: str
    """Asset filename as stored on disk (e.g. 'pepsi_30s.mp4')."""

    asset_type: str
    """Media type: 'video' | 'image' | 'html'."""

    creative_id: str | None
    """External advertiser creative identifier (optional, from creative_map)."""

    campaign_id: str | None
    """External campaign identifier (optional, from creative_map)."""

    # -- Playback --
    playlist_name: str
    """Name of the playlist that triggered the play."""

    timestamp_start: str
    """ISO 8601 UTC timestamp when playback began."""

    timestamp_end: str
    """ISO 8601 UTC timestamp when playback ended (or was interrupted)."""

    duration_planned_ms: int
    """Intended play duration in milliseconds from playlist/asset metadata."""

    duration_actual_ms: int
    """Actual wall-clock rendering time in milliseconds."""

    completion_rate: float
    """Fraction of planned duration delivered: 0.0-1.0, clamped to 1.0."""

    completed: bool
    """True when completion_rate >= 0.95 and play was not interrupted."""

    # -- Context --
    playback_context: dict
    """Ambient context: {'hour_of_day': int, 'adjacent_before': str|None, 'adjacent_after': str|None}."""

    app_version: str
    """TSV6 firmware/application version string."""

    # -- Integrity --
    schema_version: str = field(default=_SCHEMA_VERSION)
    """Schema version for forward-compatibility checks."""


# ---------------------------------------------------------------------------
# Protocol (storage backend interface)
# ---------------------------------------------------------------------------


class ImpressionRecorder(Protocol):
    """Protocol that all impression storage backends must implement.

    Future backends (Hostinger MongoDB, Vistar SSP) implement this same
    interface so callers never need to change.
    """

    def record(self, event: ImpressionEvent) -> None:
        """Enqueue or immediately persist one impression event.

        This call MUST be non-blocking from the caller's perspective.
        Implementations may buffer internally.
        """
        ...

    def flush(self) -> None:
        """Force all buffered events to be persisted to durable storage."""
        ...

    def get_metrics(self) -> dict:
        """Return a snapshot of operational metrics for health monitoring."""
        ...


# ---------------------------------------------------------------------------
# JSONL concrete implementation
# ---------------------------------------------------------------------------


class JSONLImpressionRecorder:
    """Writes one JSON object per line to date-partitioned rotating JSONL files.

    Thread-safety: record() may be called from any number of producer threads
    simultaneously.  A single background daemon thread owns all file I/O.

    File naming:
        <output_dir>/YYYY-MM-DD.jsonl          (primary)
        <output_dir>/YYYY-MM-DD.1.jsonl        (first rotation)
        <output_dir>/YYYY-MM-DD.2.jsonl        ...

    Retention: files older than retention_days are deleted on start() and
    once per hour during normal operation.
    """

    _HOURLY_RETENTION_INTERVAL_S: float = 3600.0

    def __init__(
        self,
        output_dir: Path | None = None,
        retention_days: int = 90,
        max_file_size_mb: int = 50,
        flush_interval_s: float = 5.0,
        max_buffer_size: int = 1000,
    ) -> None:
        """Initialise the recorder (does NOT start the background thread).

        Args:
            output_dir: Directory for JSONL files.  Defaults to
                ``~/.local/share/tsv6/impressions/``.
            retention_days: Delete files older than this many days.
            max_file_size_mb: Rotate current file when it exceeds this size.
            flush_interval_s: Background thread flushes open file handle every
                this many seconds.
            max_buffer_size: Maximum number of events held in the in-memory
                queue before oldest events are dropped.
        """
        if output_dir is None:
            output_dir = Path.home() / ".local" / "share" / "tsv6" / "impressions"
        self._output_dir = Path(output_dir)
        self._retention_days = retention_days
        # Clamp to at least 1 byte so _resolve_file always terminates.
        self._max_file_size_bytes = max(1, max_file_size_mb * 1024 * 1024)
        self._flush_interval_s = flush_interval_s
        self._max_buffer_size = max_buffer_size

        # Internal queue — bounded to avoid runaway memory growth.
        self._queue: queue.Queue[ImpressionEvent | None] = queue.Queue(
            maxsize=max_buffer_size
        )

        # Metrics (all protected by _metrics_lock).
        self._metrics_lock = threading.Lock()
        self._events_buffered: int = 0
        self._events_written: int = 0
        self._events_dropped: int = 0
        self._current_file: Path | None = None
        self._total_files: int = 0

        # Background thread state.
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_retention_check: float = 0.0

        # Open file handle owned exclusively by the background thread.
        self._fh: "open | None" = None  # type: ignore[type-arg]
        self._fh_path: Path | None = None
        self._fh_size: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Create output directory, enforce retention, and start background writer.

        Safe to call only once.  Calling start() again on a running recorder
        is a no-op with a warning.
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("JSONLImpressionRecorder.start() called on already-running recorder")
            return

        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._enforce_retention()
        self._last_retention_check = time.monotonic()

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="impression-writer",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "JSONLImpressionRecorder started: output_dir=%s retention_days=%d "
            "max_file_size_mb=%d flush_interval_s=%s",
            self._output_dir,
            self._retention_days,
            self._max_file_size_bytes // (1024 * 1024),
            self._flush_interval_s,
        )

    def stop(self) -> None:
        """Signal writer thread to stop, drain remaining events, and close file handle.

        Blocks until the background thread exits (max ~flush_interval_s + 1 s).
        All events in the queue at the time of stop() are guaranteed to be
        written before the thread exits.
        """
        if self._thread is None:
            return
        self._stop_event.set()
        # Unblock queue.get() in writer loop.
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=max(self._flush_interval_s + 2.0, 10.0))
        logger.info(
            "JSONLImpressionRecorder stopped: written=%d dropped=%d",
            self._events_written,
            self._events_dropped,
        )

    def record(self, event: ImpressionEvent) -> None:
        """Non-blocking enqueue of one impression event.

        If the internal buffer is full the oldest event is discarded and
        events_dropped is incremented.  This guarantees that record() never
        blocks the calling thread.

        Args:
            event: A fully-constructed ImpressionEvent.
        """
        try:
            self._queue.put_nowait(event)
            with self._metrics_lock:
                self._events_buffered += 1
            logger.debug("Impression enqueued: impression_id=%s asset_id=%s", event.impression_id, event.asset_id)
        except queue.Full:
            # Drop oldest to make room, then enqueue the new event.
            try:
                self._queue.get_nowait()
                with self._metrics_lock:
                    self._events_buffered = max(0, self._events_buffered - 1)
                    self._events_dropped += 1
                logger.warning(
                    "Impression buffer full — oldest event dropped. "
                    "impression_id=%s total_dropped=%d",
                    event.impression_id,
                    self._events_dropped,
                )
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(event)
                with self._metrics_lock:
                    self._events_buffered += 1
            except queue.Full:
                with self._metrics_lock:
                    self._events_dropped += 1
                logger.warning("Failed to enqueue impression after drop. impression_id=%s", event.impression_id)

    def flush(self) -> None:
        """Block until the queue is drained and the file handle is flushed.

        Uses a threading.Event posted to the writer loop to request a
        synchronous flush.  Returns once the flush is complete or after a
        short timeout.
        """
        flush_done = threading.Event()
        self._flush_request = flush_done
        # Wake the writer loop immediately.
        try:
            self._queue.put_nowait(None)  # sentinel to wake loop
        except queue.Full:
            pass
        flush_done.wait(timeout=self._flush_interval_s + 2.0)
        self._flush_request = None

    def get_metrics(self) -> dict:
        """Return a snapshot dict of operational metrics.

        Keys:
            events_buffered: int — events currently in the queue.
            events_written: int — cumulative events written to disk.
            events_dropped: int — cumulative events dropped due to buffer full.
            current_file: str | None — absolute path to the active JSONL file.
            total_files: int — count of JSONL files on disk.
            total_bytes_on_disk: int — sum of sizes of all JSONL files.
            oldest_file_date: str | None — date string of oldest file (YYYY-MM-DD).
        """
        with self._metrics_lock:
            buffered = self._events_buffered
            written = self._events_written
            dropped = self._events_dropped
            current = str(self._current_file) if self._current_file else None

        jsonl_files = sorted(self._output_dir.glob("*.jsonl")) if self._output_dir.exists() else []
        total_files = len(jsonl_files)
        total_bytes = sum(f.stat().st_size for f in jsonl_files if f.exists())
        oldest_date: str | None = None
        if jsonl_files:
            oldest_name = jsonl_files[0].name
            # Extract YYYY-MM-DD prefix regardless of rotation suffix.
            oldest_date = oldest_name[:10]

        return {
            "events_buffered": buffered,
            "events_written": written,
            "events_dropped": dropped,
            "current_file": current,
            "total_files": total_files,
            "total_bytes_on_disk": total_bytes,
            "oldest_file_date": oldest_date,
        }

    # ------------------------------------------------------------------
    # Internal — background writer thread
    # ------------------------------------------------------------------

    def _writer_loop(self) -> None:
        """Background thread: drain queue, write events, rotate/flush as needed."""
        last_flush = time.monotonic()
        self._flush_request: "threading.Event | None" = None

        while not self._stop_event.is_set():
            # Drain as many events as available without blocking too long.
            batch: list[ImpressionEvent] = []
            deadline = time.monotonic() + self._flush_interval_s

            while time.monotonic() < deadline:
                try:
                    item = self._queue.get(timeout=0.1)
                    if item is None:
                        # Sentinel: either flush request or stop signal.
                        break
                    batch.append(item)
                except queue.Empty:
                    break

            for event in batch:
                self._write_event(event)

            # Periodic file flush.
            now = time.monotonic()
            if self._fh is not None and (now - last_flush) >= self._flush_interval_s:
                try:
                    self._fh.flush()
                    os.fsync(self._fh.fileno())
                except OSError as exc:
                    logger.warning("Flush error: %s", exc)
                last_flush = now

            # Handle explicit flush() request from another thread.
            flush_req = getattr(self, "_flush_request", None)
            if flush_req is not None:
                if self._fh is not None:
                    try:
                        self._fh.flush()
                        os.fsync(self._fh.fileno())
                    except OSError as exc:
                        logger.warning("Explicit flush error: %s", exc)
                flush_req.set()

            # Hourly retention enforcement.
            if now - self._last_retention_check >= self._HOURLY_RETENTION_INTERVAL_S:
                self._enforce_retention()
                self._last_retention_check = now

        # Drain remaining events before exiting.
        while True:
            try:
                item = self._queue.get_nowait()
                if item is not None:
                    self._write_event(item)
            except queue.Empty:
                break

        # Final flush and close.
        if self._fh is not None:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._fh.close()
            except OSError as exc:
                logger.warning("Final flush/close error: %s", exc)
            self._fh = None
            self._fh_path = None

    def _write_event(self, event: ImpressionEvent) -> None:
        """Serialise event to JSON and append to the current JSONL file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target = self._resolve_file(today)

        try:
            if self._fh is None or self._fh_path != target:
                if self._fh is not None:
                    try:
                        self._fh.flush()
                        self._fh.close()
                    except OSError:
                        pass
                # Open in append mode — O_APPEND semantics guaranteed by "a".
                self._fh = open(target, "a", encoding="utf-8")  # noqa: WPS515
                self._fh_path = target
                self._fh_size = target.stat().st_size if target.exists() else 0
                with self._metrics_lock:
                    self._current_file = target

            line = json.dumps(asdict(event), ensure_ascii=False) + "\n"
            self._fh.write(line)
            self._fh_size += len(line.encode("utf-8"))

            with self._metrics_lock:
                self._events_buffered = max(0, self._events_buffered - 1)
                self._events_written += 1

            # Check size-based rotation after write.
            if self._fh_size >= self._max_file_size_bytes:
                logger.info(
                    "Rotating JSONL file (size %.1f MB): %s",
                    self._fh_size / (1024 * 1024),
                    self._fh_path,
                )
                try:
                    self._fh.flush()
                    self._fh.close()
                except OSError as exc:
                    logger.warning("Rotation close error: %s", exc)
                self._fh = None
                self._fh_path = None
                self._fh_size = 0

        except OSError as exc:
            logger.error("Failed to write impression event: %s", exc)
            with self._metrics_lock:
                self._events_dropped += 1

    def _resolve_file(self, date_str: str) -> Path:
        """Return the Path of the current active file for *date_str*.

        Handles size-based rotation by finding the next available suffix.
        Primary file is ``YYYY-MM-DD.jsonl``.  First rotation is
        ``YYYY-MM-DD.1.jsonl``, second is ``YYYY-MM-DD.2.jsonl``, etc.
        """
        primary = self._output_dir / f"{date_str}.jsonl"
        if not primary.exists():
            return primary

        # If we already have an open handle to a file for this date that hasn't
        # been rotated, re-use it.
        if self._fh_path is not None and self._fh_path.name.startswith(date_str):
            # Check whether it needs rotation.
            if self._fh_size < self._max_file_size_bytes:
                return self._fh_path

        # Walk rotation suffixes to find the latest file under the size limit.
        candidate = primary
        suffix = 1
        while True:
            size = candidate.stat().st_size if candidate.exists() else 0
            if size < self._max_file_size_bytes:
                return candidate
            next_candidate = self._output_dir / f"{date_str}.{suffix}.jsonl"
            candidate = next_candidate
            suffix += 1
            if suffix > 9999:  # safety valve
                logger.error("Too many rotation files for date %s", date_str)
                return candidate

    def _enforce_retention(self) -> None:
        """Delete JSONL files whose mtime is older than retention_days."""
        if not self._output_dir.exists():
            return
        cutoff = time.time() - (self._retention_days * 86400)
        for jsonl_file in self._output_dir.glob("*.jsonl"):
            try:
                if jsonl_file.stat().st_mtime < cutoff:
                    jsonl_file.unlink()
                    logger.info("Retention: deleted old impression file %s", jsonl_file.name)
            except OSError as exc:
                logger.warning("Retention: could not remove %s: %s", jsonl_file, exc)

    @property
    def total_files(self) -> int:
        """Count of JSONL files currently on disk (for testing convenience)."""
        if not self._output_dir.exists():
            return 0
        return len(list(self._output_dir.glob("*.jsonl")))
