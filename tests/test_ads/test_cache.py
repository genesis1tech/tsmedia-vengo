"""
Tests for tsv6.ads.cache — AssetCache.

Uses respx to mock HTTP downloads; runs in a temporary directory so
no actual /var/lib/tsv6 writes occur.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx
import pytest
import respx

from tsv6.ads.cache import AssetCache, _url_sha256
from tsv6.ads.config import AdConfig, DisplayAreaConfig


BASE_URL = "https://cdn.tsssp.com"
ASSET_URL = f"{BASE_URL}/creatives/hero.mp4"
ASSET_CONTENT = b"fake video content " * 100


@pytest.fixture
def tmp_cache_config(tmp_path: Path, ad_config: AdConfig) -> AdConfig:
    """Return a config with cache_dir pointing to pytest tmp_path."""
    return AdConfig(
        endpoint=ad_config.endpoint,
        network_id=ad_config.network_id,
        device_id=ad_config.device_id,
        api_key=ad_config.api_key,
        enabled=True,
        cache_dir=str(tmp_path / "ads"),
        cache_max_bytes=ad_config.cache_max_bytes,
        offline_db_path=str(tmp_path / "impressions.db"),
        offline_max_rows=ad_config.offline_max_rows,
        prefetch_lead_seconds=ad_config.prefetch_lead_seconds,
        display_area=DisplayAreaConfig(),
    )


@pytest.mark.asyncio
async def test_download_and_cache(tmp_cache_config):
    """First call downloads; second call returns from cache without HTTP."""
    with respx.mock() as router:
        router.get(ASSET_URL).mock(
            return_value=httpx.Response(200, content=ASSET_CONTENT)
        )

        cache = AssetCache(tmp_cache_config)

        path1 = await cache.get_or_download(ASSET_URL)
        assert path1.exists()
        assert path1.read_bytes() == ASSET_CONTENT

        # Second call must NOT trigger another HTTP request
        path2 = await cache.get_or_download(ASSET_URL)
        assert path2 == path1

    # Only one HTTP request was made
    assert router.calls.call_count == 1


@pytest.mark.asyncio
async def test_sha256_filename(tmp_cache_config):
    """Downloaded file is named after the SHA-256 of the URL."""
    with respx.mock() as router:
        router.get(ASSET_URL).mock(
            return_value=httpx.Response(200, content=ASSET_CONTENT)
        )

        cache = AssetCache(tmp_cache_config)
        path = await cache.get_or_download(ASSET_URL)

    expected_sha = _url_sha256(ASSET_URL)
    assert path.stem == expected_sha


@pytest.mark.asyncio
async def test_sha256_verification_mismatch(tmp_cache_config):
    """Download raises ValueError when SHA-256 does not match expected."""
    with respx.mock() as router:
        router.get(ASSET_URL).mock(
            return_value=httpx.Response(200, content=ASSET_CONTENT)
        )

        cache = AssetCache(tmp_cache_config)
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            await cache.get_or_download(ASSET_URL, expected_sha256="deadbeef" * 8)


@pytest.mark.asyncio
async def test_sha256_verification_success(tmp_cache_config):
    """Correct expected SHA-256 does not raise."""
    correct_sha = hashlib.sha256(ASSET_CONTENT).hexdigest()
    with respx.mock() as router:
        router.get(ASSET_URL).mock(
            return_value=httpx.Response(200, content=ASSET_CONTENT)
        )

        cache = AssetCache(tmp_cache_config)
        path = await cache.get_or_download(ASSET_URL, expected_sha256=correct_sha)
    assert path.exists()


@pytest.mark.asyncio
async def test_lru_eviction(tmp_path: Path, ad_config: AdConfig):
    """Oldest files are evicted when cache exceeds max_bytes."""
    # Set a tiny limit so 3 files of 100 bytes each triggers eviction
    small_config = AdConfig(
        endpoint=ad_config.endpoint,
        network_id=ad_config.network_id,
        device_id=ad_config.device_id,
        api_key=ad_config.api_key,
        enabled=True,
        cache_dir=str(tmp_path / "ads"),
        cache_max_bytes=250,  # holds 2 × 100-byte files but not 3
        offline_db_path=str(tmp_path / "impressions.db"),
        offline_max_rows=100,
        prefetch_lead_seconds=5,
        display_area=DisplayAreaConfig(),
    )

    urls = [
        f"{BASE_URL}/creatives/file{i}.mp4" for i in range(3)
    ]
    content = b"x" * 100

    with respx.mock() as router:
        for url in urls:
            router.get(url).mock(return_value=httpx.Response(200, content=content))

        cache = AssetCache(small_config)

        # Download all three files — last download should trigger eviction
        paths = []
        for url in urls:
            paths.append(await cache.get_or_download(url))

    # At least one file should have been evicted
    existing = [p for p in paths if p.exists()]
    assert len(existing) < 3, "Expected LRU eviction to remove at least one file"


@pytest.mark.asyncio
async def test_sidecar_created(tmp_cache_config):
    """A .meta.json sidecar is written alongside the cached asset."""
    with respx.mock() as router:
        router.get(ASSET_URL).mock(
            return_value=httpx.Response(200, content=ASSET_CONTENT)
        )

        cache = AssetCache(tmp_cache_config)
        path = await cache.get_or_download(ASSET_URL)

    sidecar = path.with_suffix(path.suffix + ".meta.json")
    assert sidecar.exists(), f"Sidecar not found at {sidecar}"
