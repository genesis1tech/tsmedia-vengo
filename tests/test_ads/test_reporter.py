"""
Tests for tsv6.ads.reporter — ImpressionReporter.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from tsv6.ads.reporter import ImpressionReporter, ImpressionEvent, EventType
from tsv6.ads.config import AdConfig, DisplayAreaConfig


POP_URL = "https://api.tsssp.com/api/v1/proof_of_play/pop_001"
EXP_URL = "https://api.tsssp.com/api/v1/expiration/pop_001"


def make_pop_event(play_id: str = "spot_001") -> ImpressionEvent:
    return ImpressionEvent(
        play_id=play_id,
        event_type=EventType.PROOF_OF_PLAY,
        url=POP_URL,
        payload={
            "played_at": "2026-04-18T14:22:31Z",
            "actual_duration_ms": 15020,
            "display_area_id": "main",
        },
    )


def make_exp_event(play_id: str = "spot_002") -> ImpressionEvent:
    return ImpressionEvent(
        play_id=play_id,
        event_type=EventType.EXPIRATION,
        url=EXP_URL,
        payload={"reason": "preempted_by_recycling_event"},
    )


@pytest.mark.asyncio
async def test_enqueue_and_persist(tmp_path: Path, ad_config: AdConfig):
    """Enqueued events appear in the SQLite DB."""
    db_path = str(tmp_path / "impressions.db")
    config = AdConfig(
        endpoint=ad_config.endpoint,
        network_id=ad_config.network_id,
        device_id=ad_config.device_id,
        api_key=ad_config.api_key,
        enabled=True,
        cache_dir=str(tmp_path / "ads"),
        cache_max_bytes=100_000,
        offline_db_path=db_path,
        offline_max_rows=100,
        prefetch_lead_seconds=5,
        display_area=DisplayAreaConfig(),
    )

    mock_client = MagicMock()
    mock_client.post_proof_of_play = AsyncMock()
    mock_client.post_expiration = AsyncMock()

    reporter = ImpressionReporter(config, mock_client)  # type: ignore
    await reporter.start()

    await reporter.enqueue(make_pop_event())
    await reporter.enqueue(make_exp_event())

    # Read DB directly
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT play_id, event_type FROM impression_queue") as cur:
            rows = await cur.fetchall()

    # Stop reporter (prevents background task interference)
    reporter._stop_event.set()
    if reporter._task:
        reporter._task.cancel()

    await reporter._db.close()
    reporter._db = None

    assert len(rows) == 2
    play_ids = {r[0] for r in rows}
    assert "spot_001" in play_ids
    assert "spot_002" in play_ids


@pytest.mark.asyncio
async def test_idempotent_enqueue(tmp_path: Path, ad_config: AdConfig):
    """Duplicate (play_id, event_type) pairs are silently ignored."""
    db_path = str(tmp_path / "imp.db")
    config = AdConfig(
        endpoint=ad_config.endpoint,
        network_id=ad_config.network_id,
        device_id=ad_config.device_id,
        api_key=ad_config.api_key,
        enabled=True,
        cache_dir=str(tmp_path / "ads"),
        cache_max_bytes=100_000,
        offline_db_path=db_path,
        offline_max_rows=100,
        prefetch_lead_seconds=5,
        display_area=DisplayAreaConfig(),
    )

    mock_client = MagicMock()
    mock_client.post_proof_of_play = AsyncMock()
    mock_client.post_expiration = AsyncMock()

    reporter = ImpressionReporter(config, mock_client)  # type: ignore
    await reporter.start()

    event = make_pop_event("dup_001")
    await reporter.enqueue(event)
    await reporter.enqueue(event)  # duplicate

    reporter._stop_event.set()
    if reporter._task:
        reporter._task.cancel()

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM impression_queue") as cur:
            (count,) = await cur.fetchone()  # type: ignore

    await reporter._db.close()
    reporter._db = None

    assert count == 1


@pytest.mark.asyncio
async def test_overflow_drops_oldest(tmp_path: Path, ad_config: AdConfig):
    """When max_rows is reached, the oldest row is dropped on new insert."""
    db_path = str(tmp_path / "overflow.db")
    config = AdConfig(
        endpoint=ad_config.endpoint,
        network_id=ad_config.network_id,
        device_id=ad_config.device_id,
        api_key=ad_config.api_key,
        enabled=True,
        cache_dir=str(tmp_path / "ads"),
        cache_max_bytes=100_000,
        offline_db_path=db_path,
        offline_max_rows=3,
        prefetch_lead_seconds=5,
        display_area=DisplayAreaConfig(),
    )

    mock_client = MagicMock()
    mock_client.post_proof_of_play = AsyncMock()
    mock_client.post_expiration = AsyncMock()

    reporter = ImpressionReporter(config, mock_client)  # type: ignore
    await reporter.start()

    # Insert 4 events into a queue of max_rows=3
    for i in range(4):
        await reporter.enqueue(make_pop_event(f"drop_{i:03d}"))

    reporter._stop_event.set()
    if reporter._task:
        reporter._task.cancel()

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT play_id FROM impression_queue ORDER BY id ASC"
        ) as cur:
            rows = await cur.fetchall()

    await reporter._db.close()
    reporter._db = None

    play_ids = [r[0] for r in rows]
    assert len(play_ids) == 3
    # Oldest (drop_000) should have been evicted
    assert "drop_000" not in play_ids
    assert "drop_003" in play_ids


@pytest.mark.asyncio
async def test_batched_flush(tmp_path: Path, ad_config: AdConfig):
    """Flush drains queued events by calling the API client."""
    db_path = str(tmp_path / "flush.db")
    config = AdConfig(
        endpoint=ad_config.endpoint,
        network_id=ad_config.network_id,
        device_id=ad_config.device_id,
        api_key=ad_config.api_key,
        enabled=True,
        cache_dir=str(tmp_path / "ads"),
        cache_max_bytes=100_000,
        offline_db_path=db_path,
        offline_max_rows=100,
        prefetch_lead_seconds=5,
        display_area=DisplayAreaConfig(),
    )

    mock_client = MagicMock()
    mock_client.post_proof_of_play = AsyncMock()
    mock_client.post_expiration = AsyncMock()

    reporter = ImpressionReporter(config, mock_client)  # type: ignore
    await reporter.start()

    await reporter.enqueue(make_pop_event("flush_pop"))
    await reporter.enqueue(make_exp_event("flush_exp"))

    # Manually trigger flush
    reporter._stop_event.set()  # stop background task
    if reporter._task:
        reporter._task.cancel()

    await reporter._flush_once()

    mock_client.post_proof_of_play.assert_awaited_once()
    mock_client.post_expiration.assert_awaited_once()

    # DB should be empty after successful flush
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM impression_queue") as cur:
            (count,) = await cur.fetchone()  # type: ignore

    await reporter._db.close()
    reporter._db = None

    assert count == 0


@pytest.mark.asyncio
async def test_flush_stops_at_first_failure(tmp_path: Path, ad_config: AdConfig):
    """If one send fails, subsequent rows are not dropped from the queue."""
    db_path = str(tmp_path / "fail.db")
    config = AdConfig(
        endpoint=ad_config.endpoint,
        network_id=ad_config.network_id,
        device_id=ad_config.device_id,
        api_key=ad_config.api_key,
        enabled=True,
        cache_dir=str(tmp_path / "ads"),
        cache_max_bytes=100_000,
        offline_db_path=db_path,
        offline_max_rows=100,
        prefetch_lead_seconds=5,
        display_area=DisplayAreaConfig(),
    )

    import httpx

    mock_client = MagicMock()
    mock_client.post_proof_of_play = AsyncMock(
        side_effect=httpx.NetworkError("offline")
    )
    mock_client.post_expiration = AsyncMock()

    reporter = ImpressionReporter(config, mock_client)  # type: ignore
    await reporter.start()

    await reporter.enqueue(make_pop_event("fail_001"))
    await reporter.enqueue(make_exp_event("fail_002"))

    reporter._stop_event.set()
    if reporter._task:
        reporter._task.cancel()

    await reporter._flush_once()

    # Both rows should still be in the queue
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM impression_queue") as cur:
            (count,) = await cur.fetchone()  # type: ignore

    await reporter._db.close()
    reporter._db = None

    assert count == 2
