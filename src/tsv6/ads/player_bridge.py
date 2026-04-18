"""
PlayerBridge — inserts ad media into the existing VLC MediaListPlayer
via Tk's root.after() so that all VLC mutations happen on the Tk main thread.

Design constraints:
- ZERO direct VLC calls from the asyncio thread.
- All VLC mutations dispatched with root.after(0, callable).
- Provides preempt() so the recycling state machine can pause mid-ad.
- Reports expiration to the server if the ad was < 50% played when preempted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from tsv6.ads.client import Advertisement, AdApiClient
from tsv6.ads.config import AdConfig
from tsv6.ads.reporter import EventType, ImpressionEvent, ImpressionReporter

logger = logging.getLogger(__name__)

# Type alias: the tkinter root (Any to avoid a hard tkinter import at module level)
TkRoot = Any


class PlayerBridge:
    """
    Thin adapter between asyncio AdPlayer logic and the Tk/VLC main thread.

    The bridge does NOT own the VLC instance; it receives a reference to
    the EnhancedVideoPlayer's ``list_player`` and ``root`` at construction.
    """

    def __init__(
        self,
        config: AdConfig,
        root: TkRoot,
        list_player: Any,
        reporter: ImpressionReporter,
        client: AdApiClient,
    ) -> None:
        self._config = config
        self._root = root
        self._list_player = list_player
        self._reporter = reporter
        self._client = client

        # Playback tracking
        self._current_ad: Optional[Advertisement] = None
        self._play_started_at: float = 0.0
        self._preempted: bool = False
        self._finished_callback: Optional[Callable[[], None]] = None
        # Saved media list belonging to the regular content loop — restored after each ad
        self._saved_media_list: Any = None

        # asyncio event fired when playback finishes (set from Tk thread via root.after)
        self._done_event: asyncio.Event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Provide the asyncio event loop so we can call thread-safe set()."""
        self._loop = loop

    async def play_ad(
        self,
        local_path: Path,
        advertisement: Advertisement,
        on_finished: Callable[[], None],
    ) -> None:
        """
        Play a single ad file through VLC.

        Schedules the VLC play command on the Tk thread, then waits for
        the ``_done_event`` to be set (by the VLC end-reached callback or
        by ``preempt()``).

        Args:
            local_path: Path to the locally cached creative asset.
            advertisement: The Advertisement metadata for this play.
            on_finished: Coroutine function called after playback ends.
        """
        self._current_ad = advertisement
        self._preempted = False
        self._play_started_at = 0.0
        self._done_event.clear()
        self._finished_callback = on_finished

        self._root.after(0, self._tk_start_playback, str(local_path))

        # Wait for playback to end (or preemption)
        await self._done_event.wait()

    def preempt(self) -> None:
        """
        Immediately pause ad playback because the recycling state machine
        needs the screen.  If the ad was < 50% complete, an expiration
        event is enqueued.
        """
        if not self._current_ad:
            return
        self._preempted = True
        self._root.after(0, self._tk_pause_playback)
        self._schedule_expiration_if_needed()

    def resume(self) -> None:
        """Resume a preempted ad (called when state machine returns to IDLE)."""
        if self._current_ad and self._preempted:
            self._preempted = False
            self._root.after(0, self._tk_resume_playback)

    def seconds_remaining(self) -> float:
        """Estimated seconds left in current ad (for scheduler wake-up hint)."""
        if not self._current_ad or not self._play_started_at:
            return 0.0
        elapsed = time.monotonic() - self._play_started_at
        total = float(self._current_ad.length_in_seconds)
        return max(0.0, total - elapsed)

    # ------------------------------------------------------------------
    # Tk-thread callbacks (called via root.after — never from asyncio)
    # ------------------------------------------------------------------

    def _tk_start_playback(self, path: str) -> None:
        """Called on Tk thread: save current media list, load ad, start playback."""
        try:
            import vlc  # type: ignore[import]

            # Save the regular content media list so we can restore it after the ad
            try:
                self._saved_media_list = self._list_player.get_media_list()
            except Exception:
                self._saved_media_list = None

            # Stop current playback cleanly before swapping list
            self._list_player.stop()

            instance: Any = self._list_player.get_media_player().get_instance()
            media = instance.media_new(path)
            ad_media_list = instance.media_list_new()
            ad_media_list.add_media(media)
            self._list_player.set_media_list(ad_media_list)

            # Register end-of-media callback to signal asyncio
            player = self._list_player.get_media_player()
            event_manager = player.event_manager()
            event_manager.event_attach(
                vlc.EventType.MediaPlayerEndReached,
                self._on_media_end_reached,
            )

            self._list_player.play()
            self._play_started_at = time.monotonic()
            logger.info("Ad playback started: %s", Path(path).name)
        except Exception as exc:
            logger.error("Failed to start ad playback: %s", exc)
            self._signal_done()

    def _tk_pause_playback(self) -> None:
        try:
            self._list_player.stop()
            self._restore_content_media_list()
            logger.debug("Ad playback stopped + content restored (preempted)")
        except Exception as exc:
            logger.warning("Pause failed: %s", exc)

    def _tk_resume_playback(self) -> None:
        try:
            self._list_player.play()
            logger.debug("Ad playback resumed")
        except Exception as exc:
            logger.warning("Resume failed: %s", exc)

    # Called by VLC event manager (may be on a VLC internal thread — NOT Tk thread)
    def _on_media_end_reached(self, event: Any) -> None:
        # Dispatch back to Tk thread for safety
        self._root.after(0, self._tk_on_playback_finished)

    def _tk_on_playback_finished(self) -> None:
        """Called on Tk thread when VLC signals end-of-media."""
        if self._current_ad and not self._preempted:
            elapsed_ms = int((time.monotonic() - self._play_started_at) * 1000)
            self._enqueue_proof_of_play(elapsed_ms)
        self._restore_content_media_list()
        self._signal_done()

    def _restore_content_media_list(self) -> None:
        """Restore the regular content media list after an ad finishes."""
        if self._saved_media_list is None:
            return
        try:
            self._list_player.set_media_list(self._saved_media_list)
            self._list_player.play()
            logger.debug("Restored regular content media list")
        except Exception as exc:
            logger.warning("Could not restore content media list: %s", exc)
        finally:
            self._saved_media_list = None

    def _signal_done(self) -> None:
        """Thread-safe: fire the asyncio done event from the Tk thread."""
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._done_event.set)
        if self._finished_callback:
            self._finished_callback()

    # ------------------------------------------------------------------
    # Impression reporting helpers (schedule async work from Tk thread)
    # ------------------------------------------------------------------

    def _enqueue_proof_of_play(self, actual_duration_ms: int) -> None:
        ad = self._current_ad
        if not ad or not self._loop:
            return
        import datetime

        payload = {
            "played_at": datetime.datetime.utcnow().isoformat() + "Z",
            "actual_duration_ms": actual_duration_ms,
            "display_area_id": ad.display_area_id,
            "frame_hash": None,
            "device_clock_skew_ms": None,
        }
        event = ImpressionEvent(
            play_id=ad.spot_id,
            event_type=EventType.PROOF_OF_PLAY,
            url=ad.proof_of_play_url,
            payload=payload,
        )
        asyncio.run_coroutine_threadsafe(
            self._reporter.enqueue(event), self._loop
        )

    def _schedule_expiration_if_needed(self) -> None:
        ad = self._current_ad
        if not ad or not self._loop or not self._play_started_at:
            return
        elapsed = time.monotonic() - self._play_started_at
        pct = elapsed / max(1.0, float(ad.length_in_seconds))
        if pct < 0.5:
            event = ImpressionEvent(
                play_id=ad.spot_id,
                event_type=EventType.EXPIRATION,
                url=ad.expiration_url,
                payload={"reason": "preempted_by_recycling_event"},
            )
            asyncio.run_coroutine_threadsafe(
                self._reporter.enqueue(event), self._loop
            )
            logger.info(
                "Queued expiration for ad %s (%.0f%% played)", ad.id, pct * 100
            )
