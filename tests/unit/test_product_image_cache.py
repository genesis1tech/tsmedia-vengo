"""Unit tests for the TSV6 product image cache."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from tsv6.display.tsv6_player.product_image_cache import ProductImageCache


class _FakeResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield from self._chunks


def test_path_for_url_generates_hidden_webp_filename(tmp_path: Path) -> None:
    cache = ProductImageCache(tmp_path)

    path = cache.path_for_url("https://s3.example.com/products/abc.webp?sig=123")

    assert path.parent == tmp_path
    assert path.name.startswith(".product_")
    assert path.suffix == ".webp"


def test_path_for_url_defaults_to_webp_when_extension_missing(tmp_path: Path) -> None:
    cache = ProductImageCache(tmp_path)

    path = cache.path_for_url("https://s3.example.com/products/abc")

    assert path.suffix == ".webp"


def test_resolve_for_display_returns_cached_filename_and_touches_file(tmp_path: Path) -> None:
    cache = ProductImageCache(tmp_path)
    url = "https://s3.example.com/products/abc.webp"
    path = cache.path_for_url(url)
    path.write_bytes(b"cached")
    old_time = time.time() - 100
    os.utime(path, (old_time, old_time))

    result = cache.resolve_for_display(url)

    assert result == path.name
    assert path.stat().st_mtime > old_time


def test_resolve_for_display_returns_url_and_starts_background_download(tmp_path: Path) -> None:
    cache = ProductImageCache(tmp_path)
    url = "https://s3.example.com/products/abc.webp"

    with patch.object(cache, "download_async") as download_async:
        result = cache.resolve_for_display(url)

    assert result == url
    download_async.assert_called_once_with(url)


def test_download_worker_writes_atomically_and_cleans_tmp(tmp_path: Path) -> None:
    cache = ProductImageCache(tmp_path)
    url = "https://s3.example.com/products/abc.webp"
    response = _FakeResponse([b"hello", b"", b" world"])

    with patch("tsv6.display.tsv6_player.product_image_cache.requests.get", return_value=response):
        cache._download_worker(url)

    path = cache.path_for_url(url)
    assert path.read_bytes() == b"hello world"
    assert not Path(str(path) + ".tmp").exists()


def test_download_worker_removes_tmp_on_failure(tmp_path: Path) -> None:
    cache = ProductImageCache(tmp_path)
    url = "https://s3.example.com/products/abc.webp"
    path = cache.path_for_url(url)
    tmp = Path(str(path) + ".tmp")
    tmp.write_bytes(b"partial")

    with patch(
        "tsv6.display.tsv6_player.product_image_cache.requests.get",
        side_effect=RuntimeError("network down"),
    ):
        cache._download_worker(url)

    assert not path.exists()
    assert not tmp.exists()


def test_download_async_deduplicates_active_url(tmp_path: Path) -> None:
    cache = ProductImageCache(tmp_path)
    thread = MagicMock()

    with patch("tsv6.display.tsv6_player.product_image_cache.threading.Thread", return_value=thread):
        cache.download_async("https://s3.example.com/products/abc.webp")
        cache.download_async("https://s3.example.com/products/abc.webp")

    thread.start.assert_called_once()


def test_cleanup_cache_removes_oldest_product_files_only(tmp_path: Path) -> None:
    cache = ProductImageCache(tmp_path, max_cache_size_mb=0.00015)
    oldest = tmp_path / ".product_old.webp"
    middle = tmp_path / ".product_mid.webp"
    newest = tmp_path / ".product_new.webp"
    non_product = tmp_path / "playlist.mp4"
    for index, path in enumerate((oldest, middle, newest)):
        path.write_bytes(b"x" * 100)
        timestamp = time.time() - (300 - index)
        os.utime(path, (timestamp, timestamp))
    non_product.write_bytes(b"x" * 1000)

    cache._cleanup_cache()

    assert not oldest.exists()
    assert not middle.exists()
    assert newest.exists()
    assert non_product.exists()
