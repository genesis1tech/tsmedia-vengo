"""
Tests for tsv6.ads.scheduler — AdScheduler.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tsv6.ads.client import AdPod, Advertisement
from tsv6.ads.scheduler import AdScheduler, QueuedAd
from tsv6.ads.config import AdConfig


def _make_ad(idx: int = 0) -> Advertisement:
    return Advertisement(
        id=f"ad_{idx:03d}",
        spot_id=f"spot_{idx:03d}",
        display_area_id="main",
        asset_url=f"https://cdn.tsssp.com/file{idx}.mp4",
        mime_type="video/mp4",
        width=1280,
        height=800,
        length_in_seconds=15,
        lease_expiry=9_999_999_999,
        should_expire_after=9_999_999_999,
        proof_of_play_url=f"https://api.tsssp.com/api/v1/proof_of_play/pop_{idx:03d}",
        expiration_url=f"https://api.tsssp.com/api/v1/expiration/pop_{idx:03d}",
    )


@pytest.fixture
def mock_client_with_fill():
    client = MagicMock()
    ad = _make_ad(0)
    client.request_ad_pod = AsyncMock(return_value=AdPod(advertisements=[ad]))
    return client


@pytest.fixture
def mock_client_no_fill():
    client = MagicMock()
    client.request_ad_pod = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_cache(tmp_path: Path):
    cache = MagicMock()
    cached_path = tmp_path / "ad_000.mp4"
    cached_path.write_bytes(b"fake")
    cache.get_or_download = AsyncMock(return_value=cached_path)
    return cache


@pytest.mark.asyncio
async def test_prefetch_fills_queue(ad_config, mock_client_with_fill, mock_cache):
    """Scheduler downloads the ad and makes it available via next_ad()."""
    scheduler = AdScheduler(ad_config, mock_client_with_fill, mock_cache)
    await scheduler.start()

    queued = await asyncio.wait_for(scheduler.next_ad(), timeout=5.0)

    await scheduler.stop()

    assert isinstance(queued, QueuedAd)
    assert queued.advertisement.id == "ad_000"
    assert queued.local_path.exists()


@pytest.mark.asyncio
async def test_no_fill_retries(ad_config, mock_client_no_fill, mock_cache):
    """Scheduler retries after no-fill without crashing."""
    scheduler = AdScheduler(ad_config, mock_client_no_fill, mock_cache)
    await scheduler.start()

    # Wait a bit to let the loop attempt at least one request
    await asyncio.sleep(0.1)

    assert scheduler.queue_size() == 0
    await scheduler.stop()

    assert mock_client_no_fill.request_ad_pod.call_count >= 1


@pytest.mark.asyncio
async def test_queue_capped_at_two(ad_config, mock_client_with_fill, mock_cache):
    """Scheduler stops prefetching when it already has 2 items buffered."""
    scheduler = AdScheduler(ad_config, mock_client_with_fill, mock_cache)
    await scheduler.start()

    # Let the loop run for a moment
    await asyncio.sleep(0.2)

    q_size = scheduler.queue_size()
    await scheduler.stop()

    # Should not have fetched more than 2 ahead
    assert q_size <= 2


@pytest.mark.asyncio
async def test_remaining_seconds_updates_wake_timing(
    ad_config, mock_client_with_fill, mock_cache
):
    """set_remaining_seconds() is accepted without error."""
    scheduler = AdScheduler(ad_config, mock_client_with_fill, mock_cache)
    scheduler.set_remaining_seconds(45.0)
    assert scheduler._current_ad_remaining_seconds == 45.0

    scheduler.set_remaining_seconds(-1.0)
    assert scheduler._current_ad_remaining_seconds == 0.0


@pytest.mark.asyncio
async def test_stop_unblocks_next_ad(ad_config, mock_client_no_fill, mock_cache):
    """stop() causes any blocked next_ad() awaiter to return None."""
    scheduler = AdScheduler(ad_config, mock_client_no_fill, mock_cache)
    await scheduler.start()

    async def _wait():
        return await scheduler.next_ad()

    task = asyncio.create_task(_wait())
    await asyncio.sleep(0.05)
    await scheduler.stop()

    result = await asyncio.wait_for(task, timeout=3.0)
    assert result is None
