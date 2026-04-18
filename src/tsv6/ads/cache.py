"""
Local asset cache for ad creatives.

Stores downloaded files under ``<cache_dir>/<sha256_of_url>.<ext>``.
Access time is persisted in a JSON sidecar to enable LRU eviction when
the cache exceeds ``cache_max_bytes``.

All I/O is async (httpx stream download → temp file → atomic rename).
No blocking calls on the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from tsv6.ads.config import AdConfig

logger = logging.getLogger(__name__)


def _url_sha256(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _extension_from_url(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix
    return suffix if suffix else ".bin"


class AssetCache:
    """
    Bounded LRU file cache for ad creative assets.

    Thread-safe via asyncio.Lock; safe for a single-process, single-event-loop
    deployment (the normal embedded case).
    """

    def __init__(self, config: AdConfig) -> None:
        self._cache_dir = Path(config.cache_dir)
        self._max_bytes = config.cache_max_bytes
        self._lock = asyncio.Lock()
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _asset_path(self, url: str) -> Path:
        sha = _url_sha256(url)
        ext = _extension_from_url(url)
        return self._cache_dir / f"{sha}{ext}"

    def _sidecar_path(self, asset_path: Path) -> Path:
        return asset_path.with_suffix(asset_path.suffix + ".meta.json")

    def _read_atime(self, asset_path: Path) -> float:
        sidecar = self._sidecar_path(asset_path)
        try:
            meta = json.loads(sidecar.read_text())
            return float(meta.get("atime", 0.0))
        except Exception:
            return asset_path.stat().st_mtime if asset_path.exists() else 0.0

    def _write_atime(self, asset_path: Path, atime: float) -> None:
        sidecar = self._sidecar_path(asset_path)
        try:
            sidecar.write_text(json.dumps({"atime": atime}))
        except OSError as exc:
            logger.warning("Could not write sidecar for %s: %s", asset_path, exc)

    def _total_cache_bytes(self) -> int:
        total = 0
        for p in self._cache_dir.iterdir():
            if p.is_file() and not p.name.endswith(".meta.json"):
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def _evict_lru(self) -> None:
        """Remove oldest-accessed files until we are under the byte limit."""
        entries: list[tuple[float, Path]] = []
        for p in self._cache_dir.iterdir():
            if p.is_file() and not p.name.endswith(".meta.json"):
                entries.append((self._read_atime(p), p))

        entries.sort(key=lambda t: t[0])  # oldest first

        total = sum(p.stat().st_size for _, p in entries if p.exists())
        for _, p in entries:
            if total <= self._max_bytes:
                break
            try:
                size = p.stat().st_size
                p.unlink(missing_ok=True)
                self._sidecar_path(p).unlink(missing_ok=True)
                total -= size
                logger.debug("Evicted cached asset %s", p.name)
            except OSError as exc:
                logger.warning("Eviction failed for %s: %s", p, exc)

    async def get_or_download(
        self,
        asset_url: str,
        expected_sha256: Optional[str] = None,
    ) -> Path:
        """
        Return the local path for *asset_url*, downloading it if necessary.

        Steps:
        1. Check local cache.
        2. If missing, stream-download to a temp file then atomically rename.
        3. Optionally verify SHA-256.
        4. Update access time sidecar.
        5. Run LRU eviction if cache is oversized.

        Args:
            asset_url: Absolute URL of the creative asset.
            expected_sha256: Hex SHA-256 of the file content (optional).

        Returns:
            Path to the cached local file.

        Raises:
            ValueError: SHA-256 mismatch.
            httpx.HTTPError: Download failure after retries.
        """
        async with self._lock:
            asset_path = self._asset_path(asset_url)

            if asset_path.exists():
                import time

                self._write_atime(asset_path, time.time())
                logger.debug("Cache hit: %s", asset_path.name)
                return asset_path

            logger.info("Downloading asset %s → %s", asset_url, asset_path.name)
            await self._download(asset_url, asset_path, expected_sha256)

            import time

            self._write_atime(asset_path, time.time())

            # Enforce LRU limit after each new download
            if self._total_cache_bytes() > self._max_bytes:
                self._evict_lru()

            return asset_path

    async def _download(
        self,
        url: str,
        dest: Path,
        expected_sha256: Optional[str],
    ) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._cache_dir, suffix=".tmp")
        try:
            hasher = hashlib.sha256() if expected_sha256 else None
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with os.fdopen(tmp_fd, "wb") as fh:
                        tmp_fd = -1  # ownership transferred to fh
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            fh.write(chunk)
                            if hasher:
                                hasher.update(chunk)

            if expected_sha256 and hasher:
                actual = hasher.hexdigest()
                if actual != expected_sha256:
                    raise ValueError(
                        f"SHA-256 mismatch for {url}: "
                        f"expected {expected_sha256}, got {actual}"
                    )

            # Atomic rename
            os.replace(tmp_path, dest)
            tmp_path = None  # ownership transferred
        finally:
            if tmp_fd != -1:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
            if tmp_path and Path(tmp_path).exists():
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass
