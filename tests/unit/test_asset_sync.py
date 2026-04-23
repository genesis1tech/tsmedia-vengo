"""Unit tests for :mod:`tsv6.display.tsv6_player.sync`.

All tests use ``tmp_path`` for file I/O and mock ``requests.get`` so no real
network calls are made.
"""

from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests as requests_lib

from tsv6.display.tsv6_player.sync import (
    AssetSyncState,
    AssetSyncer,
    SyncFileResult,
    SyncResult,
    SyncStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int,
    body: bytes = b"",
    headers: dict | None = None,
) -> MagicMock:
    """Build a mock ``requests.Response``."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    # iter_content yields the body in one chunk (or nothing for empty body)
    resp.iter_content = lambda chunk_size: iter([body] if body else [])
    return resp


def _make_syncer(
    tmp_path: Path,
    *,
    base_url: str = "http://test-server:3000",
    base_path: str = "/sync_folders/g1tech26/default/",
    username: str = "pi",
    password: str = "pi",
    max_concurrent: int = 4,
) -> AssetSyncer:
    """Construct an :class:`AssetSyncer` using *tmp_path* as cache dir."""
    return AssetSyncer(
        base_url=base_url,
        base_path=base_path,
        username=username,
        password=password,
        cache_dir=tmp_path,
        max_concurrent=max_concurrent,
    )


# ---------------------------------------------------------------------------
# Tests — sync_one happy path (200)
# ---------------------------------------------------------------------------


class TestSyncOneHappyPath:
    """200 response: file is written to disk and state is persisted."""

    def test_file_written_to_cache(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        body = b"video content bytes"
        resp = _make_response(
            200,
            body,
            headers={"ETag": '"abc123"', "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT"},
        )

        with patch("requests.get", return_value=resp) as mock_get:
            result = syncer.sync_one("video.mp4")

        assert result.status == SyncStatus.UPDATED
        assert result.filename == "video.mp4"
        assert result.bytes_downloaded == len(body)
        dest = tmp_path / "video.mp4"
        assert dest.exists()
        assert dest.read_bytes() == body
        # Temp file must not remain
        assert not Path(str(dest) + ".tmp").exists()

    def test_state_persisted_after_200(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        body = b"image data"
        resp = _make_response(
            200,
            body,
            headers={"ETag": '"etag-v1"', "Last-Modified": "Tue, 02 Jan 2024 00:00:00 GMT"},
        )

        with patch("requests.get", return_value=resp):
            syncer.sync_one("photo.jpg")

        state_path = tmp_path / ".sync_state.json"
        assert state_path.exists()
        raw = json.loads(state_path.read_text())
        assert "photo.jpg" in raw
        assert raw["photo.jpg"]["etag"] == '"etag-v1"'
        assert raw["photo.jpg"]["last_modified"] == "Tue, 02 Jan 2024 00:00:00 GMT"
        assert raw["photo.jpg"]["size"] == len(body)

    def test_content_length_header_not_required(self, tmp_path: Path) -> None:
        """Server may omit Content-Length; size should still be recorded."""
        syncer = _make_syncer(tmp_path)
        body = b"x" * 1024
        resp = _make_response(200, body, headers={})

        with patch("requests.get", return_value=resp):
            result = syncer.sync_one("big.bin")

        assert result.status == SyncStatus.UPDATED
        assert (tmp_path / "big.bin").stat().st_size == 1024


# ---------------------------------------------------------------------------
# Tests — 304 Not Modified
# ---------------------------------------------------------------------------


class TestSyncOne304:
    """304 response: no file write, mtime updated, state preserved."""

    def test_returns_unchanged(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)

        # Pre-seed state
        existing = AssetSyncState(
            filename="slide.png",
            etag='"old-etag"',
            last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
            size=512,
        )
        syncer._save_state_entry("slide.png", existing)

        # Pre-create the file so touch() works
        dest = tmp_path / "slide.png"
        dest.write_bytes(b"old bytes")
        original_mtime = dest.stat().st_mtime

        # Small sleep so mtime can advance
        time.sleep(0.05)

        resp = _make_response(304)
        with patch("requests.get", return_value=resp):
            result = syncer.sync_one("slide.png")

        assert result.status == SyncStatus.UNCHANGED
        assert result.bytes_downloaded == 0

    def test_304_state_not_overwritten(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        existing = AssetSyncState(
            filename="slide.png",
            etag='"original-etag"',
            last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
            size=256,
        )
        syncer._save_state_entry("slide.png", existing)
        (tmp_path / "slide.png").write_bytes(b"x" * 256)

        resp = _make_response(304)
        with patch("requests.get", return_value=resp):
            syncer.sync_one("slide.png")

        state = syncer._load_state()
        assert state["slide.png"].etag == '"original-etag"'
        assert state["slide.png"].size == 256

    def test_conditional_headers_sent(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        existing = AssetSyncState(
            filename="asset.mp4",
            etag='"etag-xyz"',
            last_modified="Wed, 03 Jan 2024 00:00:00 GMT",
            size=100,
        )
        syncer._save_state_entry("asset.mp4", existing)
        (tmp_path / "asset.mp4").write_bytes(b"y" * 100)

        resp = _make_response(304)
        with patch("requests.get", return_value=resp) as mock_get:
            syncer.sync_one("asset.mp4")

        _, kwargs = mock_get.call_args
        sent_headers = kwargs.get("headers", {})
        assert sent_headers.get("If-None-Match") == '"etag-xyz"'
        assert sent_headers.get("If-Modified-Since") == "Wed, 03 Jan 2024 00:00:00 GMT"


# ---------------------------------------------------------------------------
# Tests — 206 Partial Content (resume)
# ---------------------------------------------------------------------------


class TestSyncOneResume:
    """206 response: appends to existing .tmp and replaces dest atomically."""

    def test_partial_resume_appends(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)

        # Simulate interrupted download — .tmp already has partial data
        dest = tmp_path / "video.mp4"
        tmp_file = Path(str(dest) + ".tmp")
        partial = b"first-half-"
        tmp_file.write_bytes(partial)

        continuation = b"second-half"
        resp = _make_response(
            206,
            continuation,
            headers={"ETag": '"e1"', "Last-Modified": "Thu, 04 Jan 2024 00:00:00 GMT"},
        )

        with patch("requests.get", return_value=resp) as mock_get:
            result = syncer.sync_one("video.mp4")

        # Range header must be sent indicating where to resume
        _, kwargs = mock_get.call_args
        assert kwargs["headers"].get("Range") == f"bytes={len(partial)}-"

        assert result.status == SyncStatus.UPDATED
        assert dest.exists()
        assert dest.read_bytes() == partial + continuation
        assert not tmp_file.exists()

    def test_range_header_not_sent_when_no_tmp(self, tmp_path: Path) -> None:
        """Without a .tmp file there should be no Range header."""
        syncer = _make_syncer(tmp_path)
        resp = _make_response(200, b"full content")

        with patch("requests.get", return_value=resp) as mock_get:
            syncer.sync_one("fresh.mp4")

        _, kwargs = mock_get.call_args
        assert "Range" not in kwargs.get("headers", {})


# ---------------------------------------------------------------------------
# Tests — failure paths
# ---------------------------------------------------------------------------


class TestSyncOneFailures:
    """Connection errors and timeouts return FAILED without modifying state."""

    def test_connection_error_returns_failed(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)

        with patch(
            "requests.get",
            side_effect=requests_lib.exceptions.ConnectionError("refused"),
        ):
            result = syncer.sync_one("missing.mp4")

        assert result.status == SyncStatus.FAILED
        assert "Connection error" in result.error
        assert not (tmp_path / "missing.mp4").exists()

    def test_timeout_returns_failed(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)

        with patch(
            "requests.get",
            side_effect=requests_lib.exceptions.Timeout("timed out"),
        ):
            result = syncer.sync_one("slow.mp4")

        assert result.status == SyncStatus.FAILED
        assert "Timeout" in result.error
        assert not (tmp_path / "slow.mp4").exists()

    def test_state_unchanged_on_failure(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)

        prior = AssetSyncState(
            filename="existing.mp4",
            etag='"keep-me"',
            last_modified="Fri, 05 Jan 2024 00:00:00 GMT",
            size=999,
        )
        syncer._save_state_entry("existing.mp4", prior)

        with patch(
            "requests.get",
            side_effect=requests_lib.exceptions.ConnectionError("refused"),
        ):
            syncer.sync_one("existing.mp4")

        state = syncer._load_state()
        assert state["existing.mp4"].etag == '"keep-me"'

    def test_http_error_status_returns_failed(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        resp = _make_response(404)

        with patch("requests.get", return_value=resp):
            result = syncer.sync_one("gone.mp4")

        assert result.status == SyncStatus.FAILED
        assert "404" in result.error


# ---------------------------------------------------------------------------
# Tests — garbage collection
# ---------------------------------------------------------------------------


class TestGarbageCollection:
    """sync() removes files not in the provided filenames list."""

    def test_gc_removes_unlisted_file(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)

        # Pre-populate cache with two files
        (tmp_path / "a.jpg").write_bytes(b"a")
        (tmp_path / "b.jpg").write_bytes(b"b")

        # Pre-seed state for both
        for name in ("a.jpg", "b.jpg"):
            syncer._save_state_entry(
                name,
                AssetSyncState(filename=name, etag=None, last_modified=None, size=1),
            )

        # Only a.jpg is in the new manifest
        resp_a = _make_response(
            304,
            headers={},
        )
        with patch("requests.get", return_value=resp_a):
            result = syncer.sync(["a.jpg"])

        assert result.deleted == 1
        assert not (tmp_path / "b.jpg").exists()
        assert (tmp_path / "a.jpg").exists()

    def test_gc_preserves_state_file(self, tmp_path: Path) -> None:
        """The .sync_state.json dotfile must never be deleted by GC."""
        syncer = _make_syncer(tmp_path)

        (tmp_path / "a.jpg").write_bytes(b"a")
        syncer._save_state_entry(
            "a.jpg",
            AssetSyncState(filename="a.jpg", etag=None, last_modified=None, size=1),
        )
        (tmp_path / "b.jpg").write_bytes(b"b")
        syncer._save_state_entry(
            "b.jpg",
            AssetSyncState(filename="b.jpg", etag=None, last_modified=None, size=1),
        )

        state_file = tmp_path / ".sync_state.json"
        assert state_file.exists()

        resp = _make_response(304)
        with patch("requests.get", return_value=resp):
            syncer.sync(["a.jpg"])

        # State file preserved
        assert state_file.exists()
        # b.jpg gone
        assert not (tmp_path / "b.jpg").exists()

    def test_gc_count_in_sync_result(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)

        for name in ("keep.mp4", "delete1.mp4", "delete2.mp4"):
            (tmp_path / name).write_bytes(b"x")
            syncer._save_state_entry(
                name,
                AssetSyncState(filename=name, etag=None, last_modified=None, size=1),
            )

        resp = _make_response(304)
        with patch("requests.get", return_value=resp):
            result = syncer.sync(["keep.mp4"])

        assert result.deleted == 2


# ---------------------------------------------------------------------------
# Tests — URL encoding
# ---------------------------------------------------------------------------


class TestUrlEncoding:
    """Filenames with spaces and special characters must be percent-encoded."""

    def test_space_in_filename_encoded(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        resp = _make_response(200, b"data")

        with patch("requests.get", return_value=resp) as mock_get:
            syncer.sync_one("my video file.mp4")

        called_url: str = mock_get.call_args[0][0]
        assert "my%20video%20file.mp4" in called_url
        assert " " not in called_url

    def test_special_chars_encoded(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        resp = _make_response(200, b"data")

        with patch("requests.get", return_value=resp) as mock_get:
            syncer.sync_one("file (1) & copy.jpg")

        called_url: str = mock_get.call_args[0][0]
        assert " " not in called_url
        assert "(" not in called_url
        assert "&" not in called_url


# ---------------------------------------------------------------------------
# Tests — parallel sync
# ---------------------------------------------------------------------------


class TestParallelSync:
    """sync() with multiple files executes in parallel and aggregates results."""

    def test_all_files_synced(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path, max_concurrent=4)
        filenames = [f"file{i:02d}.mp4" for i in range(10)]
        resp = _make_response(200, b"content")

        with patch("requests.get", return_value=resp):
            result = syncer.sync(filenames)

        assert result.updated + result.unchanged == 10
        assert result.failed == 0
        for name in filenames:
            assert (tmp_path / name).exists()

    def test_aggregate_counts_correct(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path, max_concurrent=3)

        # 3 files already in cache with state → 304
        unchanged_files = [f"cached{i}.jpg" for i in range(3)]
        for name in unchanged_files:
            (tmp_path / name).write_bytes(b"old")
            syncer._save_state_entry(
                name,
                AssetSyncState(filename=name, etag='"e"', last_modified="X", size=3),
            )

        # 2 new files → 200
        new_files = ["new1.mp4", "new2.mp4"]

        def _side_effect(url, **kwargs):
            for name in unchanged_files:
                if name in url:
                    return _make_response(304)
            return _make_response(200, b"new bytes")

        with patch("requests.get", side_effect=_side_effect):
            result = syncer.sync(unchanged_files + new_files)

        assert result.unchanged == 3
        assert result.updated == 2
        assert result.failed == 0


# ---------------------------------------------------------------------------
# Tests — atomic replace (interrupted download)
# ---------------------------------------------------------------------------


class TestAtomicReplace:
    """An interrupted download must leave dest uncorrupted (no partial file)."""

    def test_tmp_exists_dest_absent_after_failure(self, tmp_path: Path) -> None:
        """Simulates Popen exit mid-write: .tmp exists but dest should not."""
        syncer = _make_syncer(tmp_path)
        dest = tmp_path / "video.mp4"
        tmp_file = Path(str(dest) + ".tmp")

        # Partially written .tmp from a previous aborted run
        tmp_file.write_bytes(b"partial")

        with patch(
            "requests.get",
            side_effect=requests_lib.exceptions.ConnectionError("refused"),
        ):
            result = syncer.sync_one("video.mp4")

        assert result.status == SyncStatus.FAILED
        # dest must not exist — the partial .tmp was not renamed
        assert not dest.exists()
        # .tmp may or may not exist (we don't clean it up on failure, intentional
        # so the next run can resume), but dest is definitively absent

    def test_dest_not_visible_until_fully_written(self, tmp_path: Path) -> None:
        """os.replace ensures dest is either the old version or fully new."""
        syncer = _make_syncer(tmp_path)
        full_body = b"complete file content"
        resp = _make_response(200, full_body)

        with patch("requests.get", return_value=resp):
            result = syncer.sync_one("atomic.mp4")

        dest = tmp_path / "atomic.mp4"
        assert result.status == SyncStatus.UPDATED
        assert dest.exists()
        assert dest.read_bytes() == full_body
        # No .tmp remains
        assert not Path(str(dest) + ".tmp").exists()


# ---------------------------------------------------------------------------
# Tests — local_path and get_metrics
# ---------------------------------------------------------------------------


class TestLocalPathAndMetrics:
    """Verify helper methods behave correctly."""

    def test_local_path_returns_cache_dir_slash_filename(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        assert syncer.local_path("foo.mp4") == tmp_path / "foo.mp4"

    def test_local_path_nonexistent_file_ok(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        path = syncer.local_path("not_yet_downloaded.mp4")
        assert path == tmp_path / "not_yet_downloaded.mp4"
        assert not path.exists()

    def test_get_metrics_empty(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        m = syncer.get_metrics()
        assert m["total_files_cached"] == 0
        assert m["total_bytes_cached"] == 0
        assert m["last_sync_at"] is None
        assert m["failed_syncs"] == 0

    def test_get_metrics_after_sync(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)
        resp = _make_response(200, b"hello world")

        with patch("requests.get", return_value=resp):
            syncer.sync(["file.mp4"])

        m = syncer.get_metrics()
        assert m["total_files_cached"] == 1
        assert m["total_bytes_cached"] == len(b"hello world")
        assert m["last_sync_at"] is not None
        assert m["failed_syncs"] == 0

    def test_get_metrics_failed_sync_increments_counter(self, tmp_path: Path) -> None:
        syncer = _make_syncer(tmp_path)

        with patch(
            "requests.get",
            side_effect=requests_lib.exceptions.ConnectionError("no route"),
        ):
            syncer.sync(["broken.mp4"])

        assert syncer.get_metrics()["failed_syncs"] == 1


# ---------------------------------------------------------------------------
# Tests — AssetSyncState dataclass
# ---------------------------------------------------------------------------


class TestAssetSyncState:
    """AssetSyncState is a frozen dataclass."""

    def test_frozen(self) -> None:
        state = AssetSyncState(
            filename="f.mp4",
            etag='"e"',
            last_modified="X",
            size=10,
        )
        with pytest.raises((AttributeError, TypeError)):
            state.size = 99  # type: ignore[misc]

    def test_optional_sha256_defaults_to_none(self) -> None:
        state = AssetSyncState(
            filename="f.mp4",
            etag=None,
            last_modified=None,
            size=0,
        )
        assert state.sha256 is None
