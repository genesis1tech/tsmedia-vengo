"""
Ad scheduler — maintains a prefetch queue of (Advertisement, local_path) pairs.

The scheduler runs as an asyncio background task.  It wakes up
``prefetch_lead_seconds`` before the current ad is due to end and requests
the next pod from the server so the next asset is ready locally before
playback resumes.

Queue is a collections.deque; the PlayerBridge pops from the left.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Optional

from tsv6.ads.cache import AssetCache
from tsv6.ads.client import AdApiClient, Advertisement
from tsv6.ads.config import AdConfig

logger = logging.getLogger(__name__)


@dataclass
class QueuedAd:
    advertisement: Advertisement
    local_path: Path


class AdScheduler:
    """
    Pre-fetches ad pods and maintains an in-memory deque ready for playback.

    Lifecycle:
        scheduler = AdScheduler(config, client, cache)
        await scheduler.start()   # begins background loop
        item = await scheduler.next_ad()  # blocks until one is available
        await scheduler.stop()
    """

    def __init__(
        self,
        config: AdConfig,
        client: AdApiClient,
        cache: AssetCache,
    ) -> None:
        self._config = config
        self._client = client
        self._cache = cache
        self._queue: Deque[QueuedAd] = deque()
        self._queue_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None
        # Seconds remaining on the currently-playing ad (injected by PlayerBridge)
        self._current_ad_remaining_seconds: float = 0.0

    def set_remaining_seconds(self, seconds: float) -> None:
        """Called by PlayerBridge to let the scheduler know how long the current ad has left."""
        self._current_ad_remaining_seconds = max(0.0, seconds)

    async def start(self) -> None:
        """Launch the background prefetch loop."""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="AdScheduler")
        logger.info("AdScheduler started")

    async def stop(self) -> None:
        """Signal the loop to exit and await it."""
        self._stop_event.set()
        self._queue_event.set()  # unblock any waiter
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        logger.info("AdScheduler stopped")

    async def next_ad(self) -> Optional[QueuedAd]:
        """
        Return the next queued ad or None if the loop has stopped.
        Blocks until an ad is available.
        """
        while not self._stop_event.is_set():
            if self._queue:
                return self._queue.popleft()
            self._queue_event.clear()
            await self._queue_event.wait()
        return None

    def queue_size(self) -> int:
        return len(self._queue)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Main prefetch loop."""
        while not self._stop_event.is_set():
            try:
                await self._maybe_prefetch()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("AdScheduler prefetch error: %s", exc)

            # Sleep until prefetch_lead_seconds before the current ad ends,
            # or 5 s if queue is empty and we want to retry quickly.
            remaining = self._current_ad_remaining_seconds
            lead = self._config.prefetch_lead_seconds
            if remaining > lead:
                sleep_for = remaining - lead
            else:
                sleep_for = 5.0

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=sleep_for
                )
            except asyncio.TimeoutError:
                pass

    async def _maybe_prefetch(self) -> None:
        """Fetch the next pod if the queue is nearly empty."""
        if len(self._queue) >= 2:
            return  # already have buffer

        pod = await self._client.request_ad_pod()
        if not pod or not pod.advertisements:
            logger.debug("No fill from server")
            return

        for ad in pod.advertisements:
            try:
                local_path = await self._cache.get_or_download(ad.asset_url)
                self._queue.append(QueuedAd(advertisement=ad, local_path=local_path))
                logger.info(
                    "Prefetched ad %s → %s", ad.id, local_path.name
                )
            except Exception as exc:
                logger.warning("Failed to cache ad %s: %s", ad.id, exc)

        if self._queue:
            self._queue_event.set()
