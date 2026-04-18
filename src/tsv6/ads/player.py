"""
AdPlayer — top-level orchestrator for tsv6.ads.

Instantiated by ProductionVideoPlayer when config.ads.enabled is True.
Runs its own asyncio event loop in a dedicated daemon thread to avoid
blocking the Tk main thread.

Public interface:
    player = AdPlayer(config, root, list_player)
    player.start()
    player.on_recycling_state_change(is_recycling=True)  # pause ads
    player.on_recycling_state_change(is_recycling=False) # resume ads
    player.stop()
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

from tsv6.ads.cache import AssetCache
from tsv6.ads.client import AdApiClient
from tsv6.ads.config import AdConfig
from tsv6.ads.player_bridge import PlayerBridge
from tsv6.ads.reporter import ImpressionReporter
from tsv6.ads.scheduler import AdScheduler, QueuedAd
from tsv6.ads.state import AdPlayerState

logger = logging.getLogger(__name__)

# Type alias (avoid hard Tk import)
TkRoot = Any


class AdPlayer:
    """
    Orchestrates the ad player lifecycle inside a dedicated asyncio loop.

    All async work runs in a background daemon thread; the Tk main thread is
    never blocked.  All VLC mutations are dispatched via root.after() inside
    PlayerBridge.
    """

    def __init__(self, config: AdConfig, root: TkRoot, list_player: Any) -> None:
        self._config = config
        self._root = root
        self._list_player = list_player
        self._state = AdPlayerState.DISABLED if not config.enabled else AdPlayerState.IDLE
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._recycling_active = False

        # Components — created inside _async_main on the dedicated loop
        self._client: Optional[AdApiClient] = None
        self._cache: Optional[AssetCache] = None
        self._scheduler: Optional[AdScheduler] = None
        self._reporter: Optional[ImpressionReporter] = None
        self._bridge: Optional[PlayerBridge] = None

    # ------------------------------------------------------------------
    # Public API (called from Tk / production_main thread)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spin up the asyncio thread and begin the ad loop."""
        if not self._config.enabled:
            logger.info("AdPlayer: disabled (TSV6_AD_ENABLED is not set)")
            return
        if not self._config.endpoint:
            logger.warning("AdPlayer: TSV6_AD_ENDPOINT not configured — ads disabled")
            return

        self._thread = threading.Thread(
            target=self._thread_main,
            name="AdPlayer",
            daemon=True,
        )
        self._thread.start()
        logger.info("AdPlayer thread started")

    def stop(self) -> None:
        """Request graceful shutdown; waits up to 5 s for the thread to exit."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("AdPlayer stopped")

    def on_recycling_state_change(self, is_recycling: bool) -> None:
        """
        Called by ProductionVideoPlayer on every state machine transition.

        When is_recycling=True the bridge pauses the current ad (and reports
        expiration if < 50% played).  When is_recycling=False ad playback
        resumes.
        """
        self._recycling_active = is_recycling
        if not self._bridge:
            return
        if is_recycling:
            self._bridge.preempt()
        else:
            self._bridge.resume()
            # Notify scheduler loop to potentially wake up sooner
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._kick_scheduler)

    def get_state(self) -> AdPlayerState:
        return self._state

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as exc:
            logger.error("AdPlayer loop error: %s", exc, exc_info=True)
        finally:
            self._loop.close()

    # ------------------------------------------------------------------
    # Async main
    # ------------------------------------------------------------------

    async def _async_main(self) -> None:
        self._stop_event = asyncio.Event()

        self._cache = AssetCache(self._config)
        self._client = AdApiClient(self._config)
        self._reporter = ImpressionReporter(self._config, self._client)
        self._bridge = PlayerBridge(
            config=self._config,
            root=self._root,
            list_player=self._list_player,
            reporter=self._reporter,
            client=self._client,
        )
        self._bridge.set_event_loop(self._loop)  # type: ignore[arg-type]

        assert self._loop is not None
        self._scheduler = AdScheduler(self._config, self._client, self._cache)

        async with self._client:
            await self._reporter.start()
            await self._scheduler.start()

            self._state = AdPlayerState.IDLE
            try:
                await self._play_loop()
            finally:
                await self._scheduler.stop()
                await self._reporter.stop()

        self._state = AdPlayerState.DISABLED

    async def _play_loop(self) -> None:
        """
        Continuously dequeue ads and play them when the device is IDLE.
        """
        assert self._stop_event is not None

        while not self._stop_event.is_set():
            # Wait until we are not in a recycling state
            if self._recycling_active:
                await asyncio.sleep(0.5)
                continue

            self._state = AdPlayerState.PREFETCHING
            assert self._scheduler is not None
            queued: Optional[QueuedAd] = await self._scheduler.next_ad()
            if queued is None:
                break  # scheduler stopped

            if self._stop_event.is_set():
                break

            self._state = AdPlayerState.PLAYING
            assert self._bridge is not None

            # Update scheduler with the expected remaining time so it can
            # wake up prefetch_lead_seconds before the end.
            self._scheduler.set_remaining_seconds(
                float(queued.advertisement.length_in_seconds)
            )

            finished_event = asyncio.Event()

            def _on_finished() -> None:
                if self._loop and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(finished_event.set)

            await self._bridge.play_ad(
                local_path=queued.local_path,
                advertisement=queued.advertisement,
                on_finished=_on_finished,
            )

            self._state = AdPlayerState.REPORTING
            # Flush is handled periodically by ImpressionReporter; no explicit
            # call needed here, but we yield to let it run.
            await asyncio.sleep(0)
            self._state = AdPlayerState.IDLE

    def _kick_scheduler(self) -> None:
        """No-op placeholder; just wakes the event loop."""
