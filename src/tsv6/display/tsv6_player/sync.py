"""Asset sync module for the TSV6 player.

Downloads and caches media assets from a PiSignage server into a local
directory, supporting conditional GET (ETag / If-Modified-Since), resumable
downloads (Range), parallel fetching, and atomic file replacement.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetSyncState:
    """Cached per-file metadata persisted between sync runs.

    Attributes:
        filename: The asset filename as served by the remote.
        etag: The ETag header value returned by the server, if any.
        last_modified: The Last-Modified header value returned by the server.
        size: The byte size of the locally cached file.
        sha256: Optional SHA-256 hex digest of the cached file content.
    """

    filename: str
    etag: str | None
    last_modified: str | None
    size: int
    sha256: str | None = None


class SyncStatus(Enum):
    """Outcome of a single-file sync attempt."""

    UNCHANGED = auto()
    UPDATED = auto()
    FAILED = auto()


@dataclass(frozen=True)
class SyncFileResult:
    """Result of syncing a single file.

    Attributes:
        filename: The asset filename that was synced.
        status: UNCHANGED, UPDATED, or FAILED.
        error: Human-readable error message when status is FAILED.
        bytes_downloaded: Number of bytes written during this sync.
    """

    filename: str
    status: SyncStatus
    error: str | None = None
    bytes_downloaded: int = 0


@dataclass(frozen=True)
class SyncResult:
    """Aggregate result returned by :meth:`AssetSyncer.sync`.

    Attributes:
        file_results: Per-file :class:`SyncFileResult` list.
        unchanged: Count of files that were already up to date.
        updated: Count of files that were downloaded or resumed.
        failed: Count of files that could not be synced.
        deleted: Count of local files removed by garbage collection.
    """

    file_results: list[SyncFileResult]
    unchanged: int
    updated: int
    failed: int
    deleted: int


# ---------------------------------------------------------------------------
# Main syncer class
# ---------------------------------------------------------------------------


class AssetSyncer:
    """Downloads and caches media assets from a PiSignage-compatible server.

    All network I/O uses the ``requests`` library. File I/O uses atomic
    rename (write to ``.tmp``, then ``os.replace``) so a crash mid-download
    never leaves a corrupt destination file.

    Args:
        base_url: Scheme + host + port, e.g. ``"https://tsmedia.g1tech.cloud"``.
        base_path: URL path prefix including trailing slash, e.g.
            ``"/sync_folders/g1tech26/default/"``.
        username: HTTP Basic auth username.
        password: HTTP Basic auth password.
        cache_dir: Local directory where assets are stored.  Created if it
            does not exist.
        state_file: Path to the JSON file that persists :class:`AssetSyncState`
            across runs.  Defaults to ``cache_dir / ".sync_state.json"``.
        max_concurrent: Maximum parallel downloads in :meth:`sync`.
        chunk_size: Stream read chunk size in bytes.
        connect_timeout: TCP connect timeout in seconds.
        read_timeout: Socket read timeout in seconds (applied per chunk).
    """

    def __init__(
        self,
        base_url: str,
        base_path: str,
        username: str,
        password: str,
        cache_dir: Path,
        state_file: Path | None = None,
        max_concurrent: int = 4,
        chunk_size: int = 65536,
        connect_timeout: float = 5.0,
        read_timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._base_path = base_path if base_path.endswith("/") else base_path + "/"
        self._auth = (username, password)
        self._cache_dir = cache_dir
        self._state_file = state_file or cache_dir / ".sync_state.json"
        self._max_concurrent = max_concurrent
        self._chunk_size = chunk_size
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Metrics
        self._last_sync_at: str | None = None
        self._failed_syncs: int = 0

        # State I/O lock — guards both reads and writes of the state file
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync(self, filenames: list[str]) -> SyncResult:
        """Sync a list of assets in parallel, then garbage-collect stale files.

        Downloads are issued in a :class:`~concurrent.futures.ThreadPoolExecutor`
        with at most ``max_concurrent`` workers.  After all downloads complete,
        any local file *not* in ``filenames`` (excluding dotfiles) is deleted.

        Args:
            filenames: Canonical asset filenames as served by the remote.

        Returns:
            A :class:`SyncResult` with per-file results and aggregate counts.
        """
        file_results: list[SyncFileResult] = []

        with ThreadPoolExecutor(max_workers=self._max_concurrent) as pool:
            future_map = {
                pool.submit(self.sync_one, fn): fn for fn in filenames
            }
            for future in as_completed(future_map):
                try:
                    result = future.result()
                except Exception as exc:
                    fn = future_map[future]
                    result = SyncFileResult(
                        filename=fn,
                        status=SyncStatus.FAILED,
                        error=str(exc),
                    )
                file_results.append(result)

        # Garbage-collect files no longer in the manifest
        deleted = self._gc(set(filenames))

        unchanged = sum(1 for r in file_results if r.status == SyncStatus.UNCHANGED)
        updated = sum(1 for r in file_results if r.status == SyncStatus.UPDATED)
        failed = sum(1 for r in file_results if r.status == SyncStatus.FAILED)

        self._last_sync_at = datetime.now(timezone.utc).isoformat()
        self._failed_syncs += failed

        return SyncResult(
            file_results=file_results,
            unchanged=unchanged,
            updated=updated,
            failed=failed,
            deleted=deleted,
        )

    def sync_one(self, filename: str) -> SyncFileResult:
        """Sync a single asset file, blocking until complete.

        Steps:
        1. Load persisted state for the file.
        2. Build the request URL with URL-encoded filename.
        3. Send conditional GET headers (``If-None-Match``, ``If-Modified-Since``)
           if prior state exists.
        4. If a ``.tmp`` file exists from a prior interrupted download, send a
           ``Range`` header to resume.
        5. Write response body to ``.tmp``, then atomically replace the dest.
        6. Persist updated state.

        Args:
            filename: Asset filename.  May contain spaces; will be
                URL-encoded before use in the request path.

        Returns:
            A :class:`SyncFileResult` indicating UPDATED, UNCHANGED, or FAILED.
        """
        dest = self._cache_dir / filename
        tmp = Path(str(dest) + ".tmp")
        url = self._build_url(filename)

        state = self._load_state()
        prior: AssetSyncState | None = state.get(filename)

        headers: dict[str, str] = {}

        # Conditional GET headers
        if prior is not None:
            if prior.etag:
                headers["If-None-Match"] = prior.etag
            if prior.last_modified:
                headers["If-Modified-Since"] = prior.last_modified

        # Resume support — only if the dest does not yet exist but .tmp does
        resume_offset = 0
        if tmp.exists() and not dest.exists():
            resume_offset = tmp.stat().st_size
            if resume_offset > 0:
                headers["Range"] = f"bytes={resume_offset}-"

        try:
            response = requests.get(
                url,
                stream=True,
                timeout=(self._connect_timeout, self._read_timeout),
                auth=self._auth,
                headers=headers,
            )
        except requests.exceptions.Timeout as exc:
            logger.error("Timeout syncing %s: %s", filename, exc)
            return SyncFileResult(
                filename=filename,
                status=SyncStatus.FAILED,
                error=f"Timeout: {exc}",
            )
        except requests.exceptions.RequestException as exc:
            logger.error("Connection error syncing %s: %s", filename, exc)
            return SyncFileResult(
                filename=filename,
                status=SyncStatus.FAILED,
                error=f"Connection error: {exc}",
            )

        # 304 Not Modified
        if response.status_code == 304:
            if dest.exists():
                dest.touch()  # Update mtime so GC skips it
            logger.debug("UNCHANGED %s (304)", filename)
            return SyncFileResult(filename=filename, status=SyncStatus.UNCHANGED)

        # Error responses
        if response.status_code not in (200, 206):
            msg = f"HTTP {response.status_code} for {url}"
            logger.error("FAILED %s: %s", filename, msg)
            return SyncFileResult(
                filename=filename,
                status=SyncStatus.FAILED,
                error=msg,
            )

        # Write to .tmp
        try:
            mode = "ab" if response.status_code == 206 else "wb"
            bytes_written = 0
            with tmp.open(mode) as fh:
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if chunk:
                        fh.write(chunk)
                        bytes_written += len(chunk)
        except OSError as exc:
            logger.error("Write error syncing %s: %s", filename, exc)
            return SyncFileResult(
                filename=filename,
                status=SyncStatus.FAILED,
                error=f"Write error: {exc}",
            )

        # Atomic replace
        os.replace(tmp, dest)

        final_size = dest.stat().st_size
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")

        new_state = AssetSyncState(
            filename=filename,
            etag=etag,
            last_modified=last_modified,
            size=final_size,
        )
        self._save_state_entry(filename, new_state)

        logger.info("UPDATED %s (%d bytes)", filename, final_size)
        return SyncFileResult(
            filename=filename,
            status=SyncStatus.UPDATED,
            bytes_downloaded=bytes_written,
        )

    def local_path(self, filename: str) -> Path:
        """Return the local cache path for a given filename.

        Does not check whether the file has been downloaded.

        Args:
            filename: Asset filename as served by the remote.

        Returns:
            Absolute :class:`~pathlib.Path` under ``cache_dir``.
        """
        return self._cache_dir / filename

    def get_metrics(self) -> dict[str, Any]:
        """Return aggregate sync metrics.

        Returns:
            A dict with keys:
            - ``total_files_cached``: Number of files currently in the state.
            - ``total_bytes_cached``: Sum of sizes of all cached files.
            - ``last_sync_at``: ISO 8601 timestamp of last :meth:`sync` call.
            - ``failed_syncs``: Cumulative count of failed file syncs since
              this instance was created.
        """
        state = self._load_state()
        total_bytes = sum(s.size for s in state.values())
        return {
            "total_files_cached": len(state),
            "total_bytes_cached": total_bytes,
            "last_sync_at": self._last_sync_at,
            "failed_syncs": self._failed_syncs,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_url(self, filename: str) -> str:
        """Construct the full download URL for *filename*, URL-encoding it.

        Args:
            filename: Raw filename, possibly containing spaces or other
                characters that must be percent-encoded in a URL path.

        Returns:
            Fully qualified URL string.
        """
        encoded = quote(filename, safe="")
        return f"{self._base_url}{self._base_path}{encoded}"

    def _load_state(self) -> dict[str, AssetSyncState]:
        """Load persisted sync state from disk.

        Returns an empty dict if the state file does not exist or is corrupt.

        Returns:
            Mapping of filename to :class:`AssetSyncState`.
        """
        with self._state_lock:
            if not self._state_file.exists():
                return {}
            try:
                raw = json.loads(self._state_file.read_text(encoding="utf-8"))
                return {
                    k: AssetSyncState(**v)
                    for k, v in raw.items()
                }
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                logger.warning("Could not parse state file %s: %s", self._state_file, exc)
                return {}

    def _save_state_entry(self, filename: str, entry: AssetSyncState) -> None:
        """Update a single entry in the persisted state file atomically.

        Args:
            filename: Key to update.
            entry: New state value.
        """
        with self._state_lock:
            state = self._load_state_unlocked()
            state[filename] = entry
            self._write_state_unlocked(state)

    def _load_state_unlocked(self) -> dict[str, AssetSyncState]:
        """Load state without acquiring the lock (caller must hold it)."""
        if not self._state_file.exists():
            return {}
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
            return {k: AssetSyncState(**v) for k, v in raw.items()}
        except (json.JSONDecodeError, TypeError, KeyError):
            return {}

    def _write_state_unlocked(self, state: dict[str, AssetSyncState]) -> None:
        """Write state to disk atomically without acquiring the lock.

        Writes to a ``.tmp`` sibling, then renames for atomicity.

        Args:
            state: Full mapping to persist.
        """
        serialisable = {k: asdict(v) for k, v in state.items()}
        tmp = Path(str(self._state_file) + ".tmp")
        tmp.write_text(
            json.dumps(serialisable, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._state_file)

    def _gc(self, keep: set[str]) -> int:
        """Delete local files whose names are not in *keep*.

        Dotfiles (names starting with ``.``) are always preserved regardless
        of whether they appear in *keep*.

        Args:
            keep: Set of asset filenames that must be retained.

        Returns:
            Number of files deleted.
        """
        deleted = 0
        with self._state_lock:
            state = self._load_state_unlocked()

            for child in list(self._cache_dir.iterdir()):
                if child.name.startswith("."):
                    continue
                if child.name not in keep:
                    try:
                        child.unlink()
                        state.pop(child.name, None)
                        deleted += 1
                        logger.info("GC: deleted %s", child.name)
                    except OSError as exc:
                        logger.warning("GC: could not delete %s: %s", child.name, exc)

            self._write_state_unlocked(state)

        return deleted

    @staticmethod
    def _sha256(path: Path) -> str:
        """Compute the SHA-256 hex digest of a file.

        Args:
            path: Path to file.

        Returns:
            Lowercase hexadecimal SHA-256 string.
        """
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
