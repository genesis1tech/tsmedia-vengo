"""Persistent local cache for scanned product images."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".webp", ".png", ".jpg", ".jpeg", ".gif"}


class ProductImageCache:
    """Cache remote product images inside the player asset directory."""

    def __init__(
        self,
        cache_dir: Path,
        max_cache_size_mb: float = 100,
        timeout: float = 10,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._max_cache_size = int(max_cache_size_mb * 1024 * 1024)
        self._timeout = timeout
        self._active_downloads: set[str] = set()
        self._lock = threading.Lock()

    def resolve_for_display(self, url: str) -> str:
        """
        Return a local cached filename on cache hit, otherwise return *url*.

        Cache misses start a background download so the next scan can use the
        local file without blocking the current product display.
        """
        cached = self.path_for_url(url)
        if cached.exists():
            self._touch(cached)
            return cached.name

        self.download_async(url)
        return url

    def path_for_url(self, url: str) -> Path:
        """Return the deterministic hidden cache path for *url*."""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self._cache_dir / f".product_{digest}{self._extension_for_url(url)}"

    def download_async(self, url: str) -> None:
        """Start a background download unless one is already in flight."""
        with self._lock:
            if url in self._active_downloads:
                return
            self._active_downloads.add(url)

        thread = threading.Thread(
            target=self._download_worker,
            args=(url,),
            name="product-image-cache",
            daemon=True,
        )
        thread.start()

    def _download_worker(self, url: str) -> None:
        target = self.path_for_url(url)
        tmp = Path(str(target) + ".tmp")
        try:
            logger.info("Caching product image: %s", url)
            response = requests.get(
                url,
                timeout=self._timeout,
                headers={"User-Agent": "TSV6-ProductImageCache/1.0"},
                stream=True,
            )
            response.raise_for_status()

            with tmp.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
            os.replace(tmp, target)
            self._touch(target)
            self._cleanup_cache()
            logger.info("Product image cached: %s", target.name)
        except Exception as exc:
            logger.warning("Product image cache download failed for %s: %s", url, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        finally:
            with self._lock:
                self._active_downloads.discard(url)

    def _cleanup_cache(self) -> None:
        if self._max_cache_size <= 0:
            return

        files = [
            path
            for path in self._cache_dir.glob(".product_*")
            if path.is_file() and not path.name.endswith(".tmp")
        ]
        total_size = sum(path.stat().st_size for path in files)
        if total_size <= self._max_cache_size:
            return

        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            try:
                size = path.stat().st_size
                path.unlink()
                total_size -= size
                logger.info("Deleted old product image cache file: %s", path.name)
            except OSError as exc:
                logger.warning("Could not delete product image cache file %s: %s", path, exc)
            if total_size <= int(self._max_cache_size * 0.8):
                break

    @staticmethod
    def _extension_for_url(url: str) -> str:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            return suffix
        return ".webp"

    @staticmethod
    def _touch(path: Path) -> None:
        os.utime(path, None)
