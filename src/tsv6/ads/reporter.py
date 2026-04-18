"""
Impression reporter with offline SQLite queue.

All DB operations use aiosqlite (non-blocking).  The schema uses a UNIQUE
index on play_id so re-enqueuing the same event is idempotent (INSERT OR
IGNORE).  The queue is bounded at offline_max_rows; when full, the oldest
rows are dropped before inserting the new one.

Flush runs every 30 seconds as an asyncio background task.  Events are
sent in batches of 50 to either proof_of_play or expiration endpoints
depending on event type.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from tsv6.ads.client import AdApiClient
from tsv6.ads.config import AdConfig

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL_SECONDS = 30
_BATCH_SIZE = 50


class EventType(str, Enum):
    PROOF_OF_PLAY = "proof_of_play"
    EXPIRATION = "expiration"


@dataclass
class ImpressionEvent:
    play_id: str
    event_type: EventType
    url: str
    payload: dict[str, Any]
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = time.time()


class ImpressionReporter:
    """
    Durable offline queue for impression events backed by SQLite.

    Lifecycle:
        reporter = ImpressionReporter(config, client)
        await reporter.start()          # opens DB, starts flush loop
        await reporter.enqueue(event)   # write to local DB
        await reporter.stop()           # flush remaining, close DB
    """

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS impression_queue (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            play_id   TEXT    NOT NULL,
            event_type TEXT   NOT NULL,
            url       TEXT    NOT NULL,
            payload   TEXT    NOT NULL,
            created_at REAL   NOT NULL,
            UNIQUE (play_id, event_type)
        );
        CREATE INDEX IF NOT EXISTS idx_created_at ON impression_queue(created_at);
    """

    def __init__(self, config: AdConfig, client: AdApiClient) -> None:
        self._db_path = Path(config.offline_db_path)
        self._max_rows = config.offline_max_rows
        self._client = client
        self._db: Optional[aiosqlite.Connection] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Open (or create) the SQLite DB and launch the flush loop."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.executescript(self._CREATE_TABLE)
        await self._db.commit()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._flush_loop(), name="ImpressionReporter")
        logger.info("ImpressionReporter started (db=%s)", self._db_path)

    async def stop(self) -> None:
        """Flush remaining events then close."""
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        # Final flush attempt
        if self._db:
            try:
                await self._flush_once()
            except Exception as exc:
                logger.warning("Final flush error: %s", exc)
            await self._db.close()
            self._db = None
        logger.info("ImpressionReporter stopped")

    async def enqueue(self, event: ImpressionEvent) -> None:
        """
        Persist an impression event to the offline queue.

        Idempotent: duplicate (play_id, event_type) pairs are silently
        ignored.  When the queue is at capacity the oldest row is dropped
        before inserting.
        """
        if not self._db:
            raise RuntimeError("ImpressionReporter not started")

        async with self._db.execute(
            "SELECT COUNT(*) FROM impression_queue"
        ) as cur:
            (count,) = await cur.fetchone()  # type: ignore[misc]

        if count >= self._max_rows:
            await self._db.execute(
                "DELETE FROM impression_queue WHERE id = "
                "(SELECT id FROM impression_queue ORDER BY created_at ASC LIMIT 1)"
            )
            logger.debug("Offline queue full — dropped oldest row")

        await self._db.execute(
            """
            INSERT OR IGNORE INTO impression_queue
                (play_id, event_type, url, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.play_id,
                event.event_type.value,
                event.url,
                json.dumps(event.payload),
                event.created_at,
            ),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=_FLUSH_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

            try:
                await self._flush_once()
            except Exception as exc:
                logger.warning("Flush error: %s", exc)

    async def _flush_once(self) -> None:
        """Drain up to _BATCH_SIZE rows per call."""
        if not self._db:
            return

        async with self._db.execute(
            "SELECT id, play_id, event_type, url, payload FROM impression_queue "
            "ORDER BY created_at ASC LIMIT ?",
            (_BATCH_SIZE,),
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            return

        logger.debug("Flushing %d impression events", len(rows))
        sent_ids: list[int] = []

        for row_id, play_id, event_type_str, url, payload_str in rows:
            try:
                payload = json.loads(payload_str)
                if event_type_str == EventType.PROOF_OF_PLAY.value:
                    await self._client.post_proof_of_play(url, payload)
                else:
                    await self._client.post_expiration(url, payload)
                sent_ids.append(row_id)
                logger.debug("Flushed %s/%s", event_type_str, play_id)
            except Exception as exc:
                logger.warning(
                    "Could not flush %s/%s: %s", event_type_str, play_id, exc
                )
                # Stop at first failure to preserve ordering
                break

        if sent_ids:
            placeholders = ",".join("?" * len(sent_ids))
            await self._db.execute(
                f"DELETE FROM impression_queue WHERE id IN ({placeholders})",
                sent_ids,
            )
            await self._db.commit()
            logger.info("Flushed %d impression events", len(sent_ids))
