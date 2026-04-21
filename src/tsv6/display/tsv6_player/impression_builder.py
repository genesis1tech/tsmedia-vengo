"""
ImpressionTracker: converts (play_start, play_end) callbacks into ImpressionEvents.

This is the primary integration point for the TSV6 player.  Callers invoke
on_play_start() when a new asset begins rendering and on_play_end() (or
on_play_interrupted()) when rendering stops.  ImpressionTracker filters out
non-ad system playlists automatically and delegates persistence to any
ImpressionRecorder backend.

Usage::

    recorder = JSONLImpressionRecorder(...)
    recorder.start()

    tracker = ImpressionTracker(
        recorder=recorder,
        player_id="TS_ABCD1234",
        installation_id="g1tech26",
        app_version="6.0.1",
        venue_id="venue-42",
    )

    imp_id = tracker.on_play_start("pepsi_30s.mp4", "tsv6_idle_loop", duration_planned_ms=30000)
    # ... asset plays ...
    tracker.on_play_end("pepsi_30s.mp4")
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsv6.display.tsv6_player.impressions import ImpressionRecorder

from tsv6.display.tsv6_player.impressions import ImpressionEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-flight play state
# ---------------------------------------------------------------------------


@dataclass
class _InFlightPlay:
    """Internal state for a play that has started but not yet ended."""

    impression_id: str
    asset_id: str
    asset_type: str
    playlist_name: str
    timestamp_start: str
    start_monotonic: float
    duration_planned_ms: int
    adjacent_before: str | None


# ---------------------------------------------------------------------------
# ImpressionTracker
# ---------------------------------------------------------------------------


class ImpressionTracker:
    """Tracks in-flight plays and converts start/end events into ImpressionEvents.

    SYSTEM_PLAYLISTS are filtered out automatically — no impression is recorded
    for assets played in those playlists.  Only playlists not in that set
    (primarily ``tsv6_idle_loop``) produce impression records.

    Thread safety: on_play_start/on_play_end may be called from the player's
    main thread.  The recorder's record() is non-blocking so this class is
    effectively non-blocking too.
    """

    SYSTEM_PLAYLISTS: frozenset[str] = frozenset(
        {
            "tsv6_processing",
            "tsv6_deposit_item",
            "tsv6_product_display",
            "tsv6_no_match",
            "tsv6_barcode_not_qr",
            "tsv6_no_item_detected",
            "tsv6_offline",
        }
    )

    # Completion rate threshold above which a play is considered "completed".
    COMPLETION_THRESHOLD: float = 0.95

    def __init__(
        self,
        recorder: "ImpressionRecorder",
        player_id: str,
        installation_id: str,
        app_version: str,
        venue_id: str | None = None,
        creative_map: dict[str, dict] | None = None,
    ) -> None:
        """Initialise the tracker.

        Args:
            recorder: Any ImpressionRecorder backend (JSONLImpressionRecorder,
                future MongoDB backend, etc.).
            player_id: CPU-serial-derived device identifier (e.g. 'TS_ABCD1234').
            installation_id: PiSignage installation/group name (e.g. 'g1tech26').
            app_version: TSV6 firmware version string.
            venue_id: Operator-assigned venue tag from env TSV6_VENUE_ID.
            creative_map: Optional mapping of asset filename to creative metadata.
                Example: ``{"pepsi_30s.mp4": {"creative_id": "CR-001",
                "campaign_id": "CAMP-42"}}``.
        """
        self._recorder = recorder
        self._player_id = player_id
        self._installation_id = installation_id
        self._app_version = app_version
        self._venue_id = venue_id
        self._creative_map: dict[str, dict] = creative_map or {}

        # asset_id -> in-flight state; keyed by asset filename.
        self._in_flight: dict[str, _InFlightPlay] = {}

        # Track the most recently seen asset for adjacent context.
        self._last_asset_id: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_play_start(
        self,
        asset_id: str,
        playlist_name: str,
        duration_planned_ms: int,
        asset_type: str = "video",
    ) -> str | None:
        """Signal that an asset has started playing.

        Args:
            asset_id: Asset filename (e.g. 'pepsi_30s.mp4').
            playlist_name: The playlist the asset is playing from.
            duration_planned_ms: Expected play duration from playlist metadata.
            asset_type: 'video' | 'image' | 'html'.

        Returns:
            The impression_id (UUID4 string) if this play will be recorded,
            or None if the playlist is a system playlist (filtered out).
        """
        if playlist_name in self.SYSTEM_PLAYLISTS:
            logger.debug(
                "on_play_start: filtered system playlist '%s' for asset '%s'",
                playlist_name,
                asset_id,
            )
            return None

        if asset_id in self._in_flight:
            # Previous play for this asset was not properly ended; orphan it.
            logger.warning(
                "on_play_start: asset '%s' already in-flight (impression_id=%s); "
                "orphaning previous play without recording.",
                asset_id,
                self._in_flight[asset_id].impression_id,
            )
            del self._in_flight[asset_id]

        impression_id = str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc)

        self._in_flight[asset_id] = _InFlightPlay(
            impression_id=impression_id,
            asset_id=asset_id,
            asset_type=asset_type,
            playlist_name=playlist_name,
            timestamp_start=now_utc.isoformat(),
            start_monotonic=_monotonic(),
            duration_planned_ms=duration_planned_ms,
            adjacent_before=self._last_asset_id,
        )
        self._last_asset_id = asset_id

        logger.debug(
            "on_play_start: impression_id=%s asset_id=%s playlist=%s",
            impression_id,
            asset_id,
            playlist_name,
        )
        return impression_id

    def on_play_end(self, asset_id: str) -> None:
        """Signal that an asset finished playing normally.

        Computes duration_actual_ms and completion_rate from the elapsed wall
        clock time since on_play_start().  Records the ImpressionEvent.

        Args:
            asset_id: Asset filename matching a previous on_play_start() call.
        """
        self._finalise(asset_id, interrupted=False)

    def on_play_interrupted(self, asset_id: str) -> None:
        """Signal that an asset was cut short (user action, playlist change, etc.).

        Records the ImpressionEvent with ``completed=False`` regardless of how
        much of the asset was actually rendered.

        Args:
            asset_id: Asset filename matching a previous on_play_start() call.
        """
        self._finalise(asset_id, interrupted=True)

    def get_in_flight(self) -> dict[str, dict]:
        """Return a copy of the current in-flight plays for debugging/metrics.

        Returns:
            Mapping of asset_id -> dict with impression_id, playlist_name,
            timestamp_start, and duration_planned_ms.
        """
        return {
            asset_id: {
                "impression_id": play.impression_id,
                "playlist_name": play.playlist_name,
                "timestamp_start": play.timestamp_start,
                "duration_planned_ms": play.duration_planned_ms,
            }
            for asset_id, play in self._in_flight.items()
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _finalise(self, asset_id: str, *, interrupted: bool) -> None:
        """Common logic for on_play_end and on_play_interrupted.

        Args:
            asset_id: Asset filename.
            interrupted: Whether the play was cut short.
        """
        play = self._in_flight.pop(asset_id, None)
        if play is None:
            logger.warning(
                "_finalise: received end/interrupt for unknown asset '%s' — no impression recorded.",
                asset_id,
            )
            return

        now_utc = datetime.now(timezone.utc)
        elapsed_ms = int((_monotonic() - play.start_monotonic) * 1000)
        duration_actual_ms = max(0, elapsed_ms)

        if play.duration_planned_ms > 0:
            raw_rate = duration_actual_ms / play.duration_planned_ms
        else:
            raw_rate = 1.0

        completion_rate = min(1.0, raw_rate)
        completed = (not interrupted) and (completion_rate >= self.COMPLETION_THRESHOLD)

        # Next asset in sequence is not yet known at this point.
        adjacent_after: str | None = None

        creative_meta = self._creative_map.get(asset_id, {})

        event = ImpressionEvent(
            impression_id=play.impression_id,
            player_id=self._player_id,
            venue_id=self._venue_id,
            installation_id=self._installation_id,
            asset_id=asset_id,
            asset_type=play.asset_type,
            creative_id=creative_meta.get("creative_id"),
            campaign_id=creative_meta.get("campaign_id"),
            playlist_name=play.playlist_name,
            timestamp_start=play.timestamp_start,
            timestamp_end=now_utc.isoformat(),
            duration_planned_ms=play.duration_planned_ms,
            duration_actual_ms=duration_actual_ms,
            completion_rate=completion_rate,
            completed=completed,
            playback_context={
                "hour_of_day": now_utc.hour,
                "adjacent_before": play.adjacent_before,
                "adjacent_after": adjacent_after,
            },
            app_version=self._app_version,
        )

        logger.debug(
            "_finalise: recording impression_id=%s asset_id=%s completion_rate=%.3f completed=%s",
            event.impression_id,
            asset_id,
            completion_rate,
            completed,
        )
        self._recorder.record(event)


# ---------------------------------------------------------------------------
# Monotonic clock helper (injectable in tests)
# ---------------------------------------------------------------------------

import time as _time_module  # noqa: E402  (import after module-level logging setup)


def _monotonic() -> float:
    """Return a monotonic time value in seconds."""
    return _time_module.monotonic()
