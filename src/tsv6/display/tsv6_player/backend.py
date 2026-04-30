"""
TSV6NativeBackend — in-process PiSignage-compatible player.

Composes four subsystems:
  - PlayerProtocolClient  (Socket.IO to server)
  - AssetSyncer           (downloads assets from server into cache_dir)
  - TSV6Renderer          (Chromium + VLC display pipeline)
  - JSONLImpressionRecorder + ImpressionTracker  (Vistar-compatible logging)

Implements the DisplayController protocol defined in
``tsv6.display.controller``.  The ``rest`` backend (PiSignageAdapter) and
this ``native`` backend are interchangeable at runtime; production_main.py
selects between them via the ``PISIGNAGE_BACKEND`` env var.

Thread-safety notes:
  - ``show_*`` methods may be called from any thread (AWS callback, etc.).
  - Renderer calls are thread-safe by design (RouterServer uses SSE fire-and-
    forget; VLC calls are guarded internally).
  - ``_idle_assets`` is written once during connect/on_config and read
    only afterwards; no lock is required.
  - ``_current_idle_asset`` is only read/written from ``show_idle`` which
    may be called from multiple threads.  It is protected by ``_idle_lock``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from tsv6.display.identity import PlayerIdentity, get_player_identity
from tsv6.display.tsv6_player.impression_builder import ImpressionTracker
from tsv6.display.tsv6_player.impressions import JSONLImpressionRecorder
from tsv6.display.tsv6_player.protocol import PlayerProtocolClient
from tsv6.display.tsv6_player.sync import AssetSyncer

# TSV6Renderer is imported lazily inside connect() to avoid pulling in Flask
# (RouterServer) at module-import time.  Tests mock the class at that path.
# A type alias is provided here for annotation purposes only.
TSV6Renderer = None  # populated lazily; see _import_renderer()

logger = logging.getLogger(__name__)

# Playlist name that is treated as the idle/attract loop.
_IDLE_PLAYLIST = "tsv6_idle_loop"


def _import_renderer():  # type: ignore[return]
    """Lazily import TSV6Renderer to avoid pulling Flask in at module-import time."""
    from tsv6.display.tsv6_player.renderer import TSV6Renderer as _Renderer
    return _Renderer

# How often the background status thread sends send_status to the server.
_STATUS_INTERVAL_S: float = 60.0  # 1 minute


class TSV6NativeBackend:
    """
    In-process PiSignage-compatible player.  Implements DisplayController.

    Parameters
    ----------
    server_url:
        Full URL of the PiSignage server, e.g. ``"https://tsmedia.g1tech.cloud"``.
    username:
        PiSignage Basic-auth username.
    password:
        PiSignage Basic-auth password.
    cache_dir:
        Local directory for downloaded assets.  Created if absent.
    layout_html:
        Absolute path to the router page HTML file (custom_layout.html).
    installation:
        PiSignage installation/group name, e.g. ``"g1tech26"``.
    group_name:
        PiSignage group name, e.g. ``"default"``.
    app_version:
        TSV6 application/firmware version string.
    venue_id:
        Operator-assigned venue tag from env ``TSV6_VENUE_ID``.
    impression_output_dir:
        Directory for JSONL impression files.  Defaults to
        ``~/.local/share/tsv6/impressions``.
    identity_override:
        Inject a fixed PlayerIdentity (useful in tests).
    """

    def __init__(
        self,
        server_url: str,
        username: str,
        password: str,
        cache_dir: Path,
        layout_html: Path,
        installation: str = "g1tech26",
        group_name: str = "default",
        app_version: str = "1.0.0",
        venue_id: str | None = None,
        impression_output_dir: Path | None = None,
        identity_override: PlayerIdentity | None = None,
    ) -> None:
        self._server_url = server_url
        self._username = username
        self._password = password
        self._cache_dir = Path(cache_dir)
        self._layout_html = Path(layout_html)
        self._installation = installation
        self._group_name = group_name
        self._app_version = app_version
        self._venue_id = venue_id
        self._impression_output_dir = impression_output_dir
        self._identity_override = identity_override

        # These are set in connect().
        self._identity: PlayerIdentity | None = None
        self._protocol: PlayerProtocolClient | None = None
        self._syncer: AssetSyncer | None = None
        self._renderer: TSV6Renderer | None = None
        self._recorder: JSONLImpressionRecorder | None = None
        self._tracker: ImpressionTracker | None = None

        # Playlist asset cache: playlist_name -> list of filenames
        self._playlist_assets: dict[str, list[str]] = {}

        # Currently in-flight idle asset for impression accounting.
        self._idle_lock = threading.Lock()
        self._current_idle_asset: str | None = None

        # Background status publishing thread.
        self._status_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False

    # ── DisplayController: Lifecycle ─────────────────────────────────────────

    def connect(self) -> bool:
        """
        Establish the Socket.IO connection to the PiSignage server.

        Builds all subsystem objects, connects the protocol client, and
        requests an immediate config push from the server.

        Returns True when the connection succeeds.
        """
        try:
            identity = self._identity_override or get_player_identity()
            self._identity = identity

            # Build sync URL: /sync_folders/<installation>/<group>/
            base_path = f"/sync_folders/{self._installation}/{self._group_name}/"

            self._syncer = AssetSyncer(
                base_url=self._server_url,
                base_path=base_path,
                username=self._username,
                password=self._password,
                cache_dir=self._cache_dir,
            )

            RendererClass = _import_renderer()
            self._renderer = RendererClass(
                cache_dir=self._cache_dir,
                layout_html=self._layout_html,
            )

            impression_dir = self._impression_output_dir or (
                Path.home() / ".local" / "share" / "tsv6" / "impressions"
            )
            self._recorder = JSONLImpressionRecorder(output_dir=impression_dir)

            self._tracker = ImpressionTracker(
                recorder=self._recorder,
                player_id=identity.player_name,
                installation_id=self._installation,
                app_version=self._app_version,
                venue_id=self._venue_id,
            )

            self._protocol = PlayerProtocolClient(
                server_url=self._server_url,
                cpu_serial=identity.cpu_serial,
                player_name=identity.player_name,
                on_config=self._on_config,
                on_sync=self._on_sync,
                on_setplaylist=self._on_setplaylist,
            )

            connected = self._protocol.connect()
            if not connected:
                logger.error("TSV6NativeBackend: protocol connect failed")
                return False

            # Request an immediate config push.
            self._protocol.request_reconfig()

            logger.info(
                "TSV6NativeBackend connected: player=%s server=%s",
                identity.player_name,
                self._server_url,
            )
            return True

        except Exception as exc:
            logger.error("TSV6NativeBackend.connect() failed: %s", exc)
            return False

    def disconnect(self) -> None:
        """Gracefully tear down the Socket.IO connection."""
        if self._protocol is not None:
            try:
                self._protocol.disconnect()
            except Exception as exc:
                logger.warning("Protocol disconnect error: %s", exc)

    def start(self) -> None:
        """
        Start the renderer, impression recorder, and status publishing thread.

        Must be called after connect() succeeds.
        """
        if self._started:
            logger.warning("TSV6NativeBackend.start() called twice — ignoring")
            return

        if self._recorder is not None:
            self._recorder.start()

        if self._renderer is not None:
            ok = self._renderer.start()
            if not ok:
                logger.error("TSV6Renderer failed to start")

        self._stop_event.clear()
        self._status_thread = threading.Thread(
            target=self._status_loop,
            name="tsv6-native-status",
            daemon=True,
        )
        self._status_thread.start()

        self._started = True
        logger.info("TSV6NativeBackend started")

    def stop(self) -> None:
        """Stop all background services and release resources.  Idempotent."""
        if not self._started:
            return
        self._started = False  # Mark stopped first to prevent re-entry.
        self._stop_event.set()

        if self._status_thread is not None:
            self._status_thread.join(timeout=10.0)
            self._status_thread = None

        if self._renderer is not None:
            try:
                self._renderer.stop()
            except Exception as exc:
                logger.warning("Renderer stop error: %s", exc)

        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception as exc:
                logger.warning("Recorder stop error: %s", exc)

        if self._protocol is not None:
            try:
                self._protocol.disconnect()
            except Exception as exc:
                logger.warning("Protocol stop/disconnect error: %s", exc)

        logger.info("TSV6NativeBackend stopped")

    # ── DisplayController: State Query ───────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """True when both the protocol client and renderer are operational."""
        protocol_ok = (
            self._protocol is not None and self._protocol.is_connected()
        )
        renderer_ok = (
            self._renderer is None or self._renderer.is_connected
        )
        return protocol_ok and renderer_ok

    def get_metrics(self) -> dict:
        """Return merged metrics from all four subsystems."""
        result: dict = {}

        if self._protocol is not None:
            proto = self._protocol.get_metrics()
            result.update({f"protocol_{k}": v for k, v in proto.items()})

        if self._syncer is not None:
            sync = self._syncer.get_metrics()
            result.update({f"sync_{k}": v for k, v in sync.items()})

        if self._renderer is not None:
            rend = self._renderer.get_metrics()
            result.update({f"renderer_{k}": v for k, v in rend.items()})

        if self._recorder is not None:
            imp = self._recorder.get_metrics()
            result.update({f"impression_{k}": v for k, v in imp.items()})

        return result

    # ── DisplayController: Display States ────────────────────────────────────

    def show_idle(self) -> bool:
        """
        Switch to the idle/attract loop — Vengo ads or VLC fallback.

        Resolves MP4 paths from the tsv6_idle_loop playlist cache, hands
        them to the renderer, and starts impression tracking for each asset.
        """
        self._interrupt_current_idle()

        if self._renderer is None:
            return False

        # Vengo ad server is the primary idle display
        from tsv6.config.config import config
        if config.vengo.enabled:
            url = self._build_vengo_url()
            if url:
                ok = self._renderer.show_vengo_idle(url)
                if ok:
                    return True
            # URL build failed — fall through to VLC idle
            logger.warning("Vengo URL build failed, falling back to VLC idle")

        # Fallback: VLC idle loop (PiSignage-pushed assets)
        mp4_paths = self._resolve_idle_mp4s()
        if not mp4_paths:
            logger.warning("show_idle: no MP4 assets found in idle loop playlist")
            return False

        ok = self._renderer.show_idle(mp4_paths)
        if ok and mp4_paths:
            # Track impression for the first asset in the loop.
            asset_id = mp4_paths[0].name
            with self._idle_lock:
                self._current_idle_asset = asset_id
            if self._tracker is not None:
                self._tracker.on_play_start(
                    asset_id=asset_id,
                    playlist_name=_IDLE_PLAYLIST,
                    duration_planned_ms=30_000,  # default if not known
                    asset_type="video",
                )
        return ok

    def show_processing(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'Verifying...' screen by playing the processing playlist."""
        return self._play_state_playlist(
            playlist_override or "tsv6_processing", state="processing"
        )

    def show_deposit_item(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'Please Deposit Your Item' screen.

        Loops the playlist for the duration of the recycle transaction.
        Unlike the terminal state playlists (no_match, no_item_detected,
        barcode_not_qr) which auto-return to idle, deposit_item is a
        transitional state — it's swapped out by the success path
        (show_product_display) or by a failure handler.  Looping prevents
        the screen from briefly returning to idle if the playlist's MP4(s)
        are shorter than the door-open hold time.
        """
        return self._play_state_playlist(
            playlist_override or "tsv6_deposit_item",
            state="deposit_item",
            loop=True,
        )

    def show_product_display(
        self,
        product_image_path: str,
        qr_url: str,
        nfc_url: str | None = None,
        playlist_override: str | None = None,
        product_name: str = "",
        product_brand: str = "",
        product_desc: str = "",
    ) -> bool:
        """
        Switch to the product result screen.

        ``product_image_path`` may be a local filename, a remote ``http(s)://``
        URL, or empty — when empty the renderer falls back to a text-only card
        built from ``product_name`` / ``product_brand`` / ``product_desc`` (V2
        cold-UPC first-scan path).

        ``playlist_override`` accepted for ``DisplayController`` parity; ignored
        by the native renderer.
        """
        self._interrupt_current_idle()
        if self._renderer is None:
            return False
        # Pass the path/URL through verbatim so the renderer can decide whether
        # to treat it as a filename or a remote URL. Wrapping every input in
        # Path() (the previous behavior) silently dropped the URL scheme and
        # left only the basename.
        if not product_image_path or product_image_path in ("None", "null"):
            image_arg: "Path | str | None" = None
        elif "://" in product_image_path:
            image_arg = product_image_path
        else:
            image_arg = Path(product_image_path)
        logger.info(
            "show_product_display: image=%r qr_url=%r product_name=%r",
            image_arg,
            qr_url,
            product_name,
        )
        ok = self._renderer.show_product_display(
            image_path=image_arg,
            qr_url=qr_url,
            nfc_url=nfc_url,
            product_name=product_name,
            product_brand=product_brand,
            product_desc=product_desc,
        )
        if ok:
            self._schedule_product_return_to_idle()
        return ok

    def _schedule_product_return_to_idle(self) -> None:
        """Return to the idle attract loop after the product-display window.

        The product screen is HTML (no MP4 EndReached event), so unlike state
        playlists it has no built-in auto-return.  Without this scheduler the
        product card would stick on screen forever.

        Duration is tunable via ``TSV6_PRODUCT_DISPLAY_DURATION_SECS``
        (default 15s).  The state-guard in ``_async_return_to_idle`` makes
        this safe against a follow-up scan: if the renderer has already
        moved on to a newer state when the timer fires, the return-to-idle
        is skipped.
        """
        try:
            duration = float(os.environ.get("TSV6_PRODUCT_DISPLAY_DURATION_SECS", "3.5"))
        except ValueError:
            duration = 5.0
        threading.Thread(
            target=self._delayed_return_to_idle,
            args=("product", duration),
            name="tsv6-product-return-to-idle",
            daemon=True,
        ).start()

    def _delayed_return_to_idle(self, expected_state: str, delay: float) -> None:
        """Sleep ``delay`` seconds, then return to idle if state hasn't changed."""
        try:
            time.sleep(delay)
            if self._renderer is None:
                return
            current = self._renderer.get_metrics().get("state", "")
            if current == expected_state:
                logger.info(
                    "Product display window elapsed (%.1fs) — returning to idle.",
                    delay,
                )
                self.show_idle()
            else:
                logger.info(
                    "Product return-to-idle skipped: renderer now in %r (was %r).",
                    current,
                    expected_state,
                )
        except Exception as exc:
            logger.warning("Delayed return to idle failed: %s", exc)

    def show_no_match(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'Unrecognized Barcode' screen via MP4 playlist."""
        return self._play_state_playlist(
            playlist_override or "tsv6_no_match", state="no_match"
        )

    def show_barcode_not_qr(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'QR Code Detected — Use Barcode' screen via MP4 playlist."""
        return self._play_state_playlist(
            playlist_override or "tsv6_barcode_not_qr", state="barcode_not_qr"
        )

    def show_no_item_detected(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'Item Not Detected' screen via MP4 playlist."""
        return self._play_state_playlist(
            playlist_override or "tsv6_no_item_detected", state="no_item_detected"
        )

    def _play_state_playlist(
        self, playlist_name: str, state: str, loop: bool = False
    ) -> bool:
        """
        Resolve a playlist's local MP4(s) and play them once via VLC.

        Used by every transient state (deposit_item, processing, no_match,
        no_item_detected, barcode_not_qr) so the device renders state screens
        the same way it renders the idle loop. Eliminates the need for
        per-state HTML asset files.

        The playlist plays **once** (no loop).  When the last item finishes,
        the display automatically returns to the idle attract loop.

        If the playlist has no MP4s synced down to the local cache (e.g. the
        playlist isn't assigned to this device's group on the media server)
        ``play_video_loop`` will log a warning and the screen will fall back
        to whatever was last drawn — which is fine for short transient states.
        """
        self._interrupt_current_idle()
        if self._renderer is None:
            return False
        mp4_paths = self._resolve_playlist_mp4s(playlist_name)
        if not mp4_paths:
            logger.warning(
                "show %s: no MP4 assets cached for playlist %r — "
                "ensure the playlist is assigned to this device's group "
                "on the media server.",
                state,
                playlist_name,
            )
            return False

        def _return_to_idle() -> None:
            """Auto-return to idle after the state playlist finishes.

            Scheduled on a background thread to avoid calling show_idle()
            from within the VLC EndReached callback (which fires on the Tk
            thread).  Stopping/destroying a Tk window from its own thread
            causes deadlocks and leaves the renderer state stuck.

            Only returns to idle if the renderer is still in the expected
            state — avoids clobbering a newer state (e.g. openDoor arrives
            while processing is still playing).
            """
            logger.info("State playlist %r finished — checking for return to idle.", state)
            threading.Thread(
                target=self._async_return_to_idle,
                args=(state,),
                name="tsv6-return-to-idle",
                daemon=True,
            ).start()

        # Looping playlists don't auto-return to idle — the caller is
        # expected to swap to a different screen explicitly (e.g. the
        # deposit_item loop is ended by show_product_display on success).
        return self._renderer.play_video_loop(
            mp4_paths,
            state=state,
            loop=loop,
            on_end=None if loop else _return_to_idle,
        )

    def show_offline(self) -> bool:
        """Switch to the offline fallback screen."""
        self._interrupt_current_idle()
        if self._renderer is None:
            return False
        return self._renderer.show_offline()

    def _async_return_to_idle(self, expected_state: str) -> None:
        """Thread-safe wrapper to return to idle from a VLC callback.

        Only transitions to idle if the renderer is still in
        *expected_state*, so a newer state (e.g. openDoor → deposit_item)
        is not clobbered.
        """
        try:
            time.sleep(0.3)  # Brief delay to let VLC's EndReached cleanup finish
            if self._renderer is not None:
                current = self._renderer.get_metrics().get("state", "")
                if current == expected_state:
                    logger.info(
                        "Renderer still in %r — returning to idle.", expected_state
                    )
                    self.show_idle()
                else:
                    logger.info(
                        "Renderer moved to %r (was %r) — skipping return to idle.",
                        current,
                        expected_state,
                    )
        except Exception as exc:
            logger.warning("Async return to idle failed: %s", exc)

    # ── Protocol callbacks ────────────────────────────────────────────────────

    def _on_config(self, config_obj: dict) -> None:
        """
        Handle the ``config`` event from the server.

        Triggers asset sync for all assets listed in the config and caches
        per-playlist asset lists.
        """
        logger.info("TSV6NativeBackend: received config event")
        try:
            assets: list[str] = config_obj.get("assets", [])
            if assets and self._syncer is not None:
                logger.info("Config: syncing %d asset(s)", len(assets))
                result = self._syncer.sync(assets)
                logger.info(
                    "Asset sync complete: updated=%d unchanged=%d failed=%d",
                    result.updated,
                    result.unchanged,
                    result.failed,
                )

            # Cache playlist -> asset mappings from the config IF AND ONLY IF
            # the config event's per-playlist dicts include them. PiSignage's
            # config event does NOT carry per-playlist asset lists — each
            # playlist dict is just {name, settings, plType, ...}. The actual
            # per-playlist file (`__{name}.json`) is pushed separately by the
            # asset sync above. Calling _write_playlist_cache(name, []) here
            # would clobber that legitimate file with an empty array, which is
            # exactly the bug that left every state playlist unable to resolve
            # its MP4s on this device.
            playlists: list[dict] = config_obj.get("playlists", [])
            for playlist in playlists:
                name = playlist.get("name", "")
                pl_assets: list[str] = playlist.get("assets", []) or []
                if name and pl_assets:
                    self._playlist_assets[name] = pl_assets
                    self._write_playlist_cache(name, pl_assets)

            # Apply ticker settings from the config event.
            ticker = config_obj.get("groupTicker") or config_obj.get("ticker")
            if ticker:
                self._apply_ticker(ticker)

        except Exception as exc:
            logger.error("_on_config error: %s", exc)

    def _apply_ticker(self, ticker: dict) -> None:
        """Push server ticker settings to the renderer footer."""
        logger.info("_apply_ticker received: %r", ticker)
        if self._renderer is None or not isinstance(ticker, dict):
            return
        enabled = bool(ticker.get("enable"))
        text = str(ticker.get("messages") or "").strip()
        behavior = str(ticker.get("behavior") or "")
        try:
            speed = int(ticker.get("textSpeed") or 3)
        except (TypeError, ValueError):
            speed = 3

        # Ticker height — must be parsed early so font-size pct calc can use it.
        ticker_height = 0
        try:
            ticker_height = int(ticker.get("tickerHeight") or 0)
        except (TypeError, ValueError):
            ticker_height = 0

        # Font family: server may send as "fontFamily", "tickerFont", or
        # a custom override "fontFamilyCustom" which wins when set.
        font_family = str(
            ticker.get("fontFamilyCustom")
            or ticker.get("fontFamily")
            or ticker.get("tickerFont")
            or ""
        ).strip()
        try:
            font_size_pct = float(ticker.get("fontSizePct") or 0)
        except (TypeError, ValueError):
            font_size_pct = 0.0
        # Server may also send an absolute font-size in px via "tickerFontSizeCss".
        # Convert to a percentage of the ticker height for the renderer.
        if not font_size_pct:
            try:
                abs_px = float(ticker.get("tickerFontSizeCss") or 0)
                if abs_px > 0 and ticker_height > 0:
                    font_size_pct = (abs_px / ticker_height) * 100
            except (TypeError, ValueError):
                pass
        color = str(ticker.get("color") or "").strip()
        background = str(ticker.get("background") or ticker.get("backgroundColor") or "").strip()
        bold = bool(ticker.get("bold"))
        italic = bool(ticker.get("italic"))
        try:
            font_weight = int(ticker.get("fontWeight")) if ticker.get("fontWeight") is not None else 0
        except (TypeError, ValueError):
            font_weight = 0
        custom_css = str(ticker.get("customCss") or ticker.get("style") or "").strip()

        logger.info(
            "_apply_ticker -> show_ticker(text=%r, enabled=%s, scroll=%s, speed=%s, "
            "font=%r, size_pct=%s, color=%r, bg=%r, bold=%s, italic=%s, weight=%s, css=%r, ticker_h=%s)",
            text, enabled and bool(text), behavior in ("scroll", "slide"), speed,
            font_family, font_size_pct, color, background, bold, italic, font_weight,
            custom_css, ticker_height,
        )
        self._renderer.show_ticker(
            text=text,
            enabled=enabled and bool(text),
            scroll=(behavior in ("scroll", "slide")),
            speed=speed,
            font_family=font_family,
            font_size_pct=font_size_pct,
            color=color,
            background=background,
            bold=bold,
            italic=italic,
            font_weight=font_weight,
            custom_css=custom_css,
            ticker_height=ticker_height,
        )

    def _on_sync(
        self,
        playlists: list[str],
        assets: list[str],
        ticker: dict | None = None,
    ) -> None:
        """
        Handle the ``sync`` event from the server.

        Triggers a fresh asset sync for the listed assets and applies the
        ticker configuration to the footer.
        """
        logger.info(
            "TSV6NativeBackend: received sync event — %d playlist(s), %d asset(s)",
            len(playlists),
            len(assets),
        )
        if ticker is not None:
            self._apply_ticker(ticker)
        if assets and self._syncer is not None:
            try:
                result = self._syncer.sync(assets)
                logger.info(
                    "Sync event complete: updated=%d unchanged=%d failed=%d",
                    result.updated,
                    result.unchanged,
                    result.failed,
                )
                # Restart the idle loop so VLC picks up added/removed MP4s.
                if result.updated or result.failed == 0:
                    try:
                        self.show_idle()
                    except Exception as exc:
                        logger.warning("show_idle after sync failed: %s", exc)
            except Exception as exc:
                logger.error("_on_sync asset sync error: %s", exc)

    def _on_setplaylist(self, playlist_name: str) -> str:
        """
        Handle the ``setplaylist`` event from the server.

        TSV6 only allows the PiSignage server to drive the **idle loop** (and
        the ``tsv6_offline`` fallback). Every other ``tsv6_*`` playlist is a
        per-scan transient state and is fired exclusively by the V2 device-side
        flow in production_main when an openDoor / noMatch / qrCode response
        arrives. Honoring server-driven rotation through state playlists would
        cause the screen to cycle through every state regardless of scanner
        activity (which it did before this guard — see commit message).
        """
        logger.info("TSV6NativeBackend: setplaylist -> %s", playlist_name)
        try:
            if playlist_name == _IDLE_PLAYLIST:
                self.show_idle()
            elif playlist_name == "tsv6_offline":
                self.show_offline()
            elif playlist_name in (
                "tsv6_processing",
                "tsv6_deposit_item",
                "tsv6_no_match",
                "tsv6_barcode_not_qr",
                "tsv6_no_item_detected",
                "tsv6_product_display",
            ):
                logger.info(
                    "Ignoring server-driven setplaylist for transient state %r — "
                    "this state is fired only by the device's V2 scan flow.",
                    playlist_name,
                )
            else:
                logger.warning("Unknown playlist name: %s", playlist_name)
        except Exception as exc:
            logger.error("_on_setplaylist error for %s: %s", playlist_name, exc)

        return f"Playing {playlist_name}"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _interrupt_current_idle(self) -> None:
        """
        End any in-flight idle impression before transitioning to another state.

        Called at the start of every ``show_*`` method (including show_idle
        itself, which replaces a previous idle cycle).
        """
        with self._idle_lock:
            asset_id = self._current_idle_asset
            self._current_idle_asset = None

        if asset_id is not None and self._tracker is not None:
            try:
                self._tracker.on_play_interrupted(asset_id)
            except Exception as exc:
                logger.warning(
                    "_interrupt_current_idle: on_play_interrupted error for %s: %s",
                    asset_id,
                    exc,
                )

    def _build_vengo_url(self) -> str:
        """Build the Vengo web player URL for this device."""
        from tsv6.config.config import config
        from urllib.parse import quote

        vc = config.vengo
        if not self._identity:
            return ""

        ad_unit_id = vc.ad_unit_id_override or self._identity.player_name
        url = (
            f"{vc.web_player_base_url}"
            f"?organization_id={vc.organization_id}"
            f"&ad_unit_id={ad_unit_id}"
        )
        if vc.no_ad_url:
            url += f"&no_ad_url={quote(vc.no_ad_url, safe='')}"
        return url

    def _resolve_playlist_mp4s(
        self, playlist_name: str, fallback_to_any_mp4: bool = False
    ) -> list[Path]:
        """
        Return the list of local MP4 paths for ``playlist_name``.

        Reads ``{cache_dir}/__{playlist_name}.json`` if present. If the cache
        file is missing or contains no MP4s and ``fallback_to_any_mp4`` is set,
        scans the cache_dir for any ``*.mp4`` (used by the idle loop so an
        unconfigured device still has something to show).
        """
        cached = self._load_playlist_cache(playlist_name)
        mp4_names: list[str] = (
            [f for f in cached if f.lower().endswith(".mp4")]
            if cached
            else []
        )

        if not mp4_names and fallback_to_any_mp4 and self._cache_dir.exists():
            mp4_names = sorted(
                p.name for p in self._cache_dir.glob("*.mp4")
            )

        return [
            self._cache_dir / name
            for name in mp4_names
            if (self._cache_dir / name).exists()
        ]

    def _resolve_idle_mp4s(self) -> list[Path]:
        """Idle-loop convenience wrapper — falls back to any cached MP4."""
        return self._resolve_playlist_mp4s(_IDLE_PLAYLIST, fallback_to_any_mp4=True)

    def _write_playlist_cache(self, playlist_name: str, assets: list[str]) -> None:
        """
        Persist a playlist's asset list to ``{cache_dir}/__{playlist_name}.json``.

        Uses atomic write (tmp → rename) to prevent corrupt reads.
        """
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_dir / f"__{playlist_name}.json"
        tmp = Path(str(target) + ".tmp")
        try:
            tmp.write_text(json.dumps(assets, indent=2), encoding="utf-8")
            tmp.replace(target)
        except OSError as exc:
            logger.warning("Could not write playlist cache %s: %s", target, exc)

    def _load_playlist_cache(self, playlist_name: str) -> list[str] | None:
        """
        Load a playlist's asset list from ``{cache_dir}/__{playlist_name}.json``.

        Accepts both shapes seen in the wild:
        * a bare list of filenames (the legacy shape this module wrote itself)
        * the PiSignage-native dict shape pushed by ``tsmedia.g1tech.cloud``,
          which has keys like ``files`` / ``assets`` / ``items`` containing
          either bare filename strings or dicts with a ``filename`` key.

        Returns ``None`` if the file does not exist, is unreadable, or contains
        no recognisable filename list.
        """
        target = self._cache_dir / f"__{playlist_name}.json"
        if not target.exists():
            return None
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read playlist cache %s: %s", target, exc)
            return None

        candidate_list: list | None = None
        if isinstance(data, list):
            candidate_list = data
        elif isinstance(data, dict):
            for key in ("files", "assets", "items", "playlist", "filenames"):
                value = data.get(key)
                if isinstance(value, list):
                    candidate_list = value
                    break

        if candidate_list is None:
            logger.warning(
                "Playlist cache %s has unrecognised shape (top-level keys=%s); "
                "treating as empty.",
                target.name,
                list(data.keys()) if isinstance(data, dict) else type(data).__name__,
            )
            return None

        out: list[str] = []
        for item in candidate_list:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                # PiSignage entries typically look like {"filename": "x.mp4", ...}
                # but defensively also accept "name" / "asset" / "url".
                for key in ("filename", "name", "asset", "url", "file"):
                    val = item.get(key)
                    if isinstance(val, str) and val:
                        out.append(val)
                        break
        return out or None

    def _build_status_payload(self) -> dict:
        """Build the runtime status dict for send_status."""
        disk_used = 0
        try:
            disk_used = int(
                shutil.disk_usage(str(self._cache_dir)).used / (1024 * 1024)
            )
        except OSError:
            pass

        pi_temp = 0.0
        try:
            import psutil  # deferred: psutil sensor calls may hang on non-Linux
            temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
            if temps:
                for sensor_list in temps.values():
                    if sensor_list:
                        pi_temp = sensor_list[0].current
                        break
        except (AttributeError, OSError, Exception):
            pass

        current_playlist = _IDLE_PLAYLIST
        if self._renderer is not None:
            state = self._renderer.get_metrics().get("state", "idle")
            if state != "idle":
                current_playlist = f"tsv6_{state}"

        return {
            "currentPlaylist": current_playlist,
            "playlistOn": True,
            "syncInProgress": False,
            "diskSpaceUsed": disk_used,
            "piTemperature": pi_temp,
            "uptime": int(time.monotonic()),
        }

    def _status_loop(self) -> None:
        """Background thread: publish status to the server every 5 minutes."""
        while not self._stop_event.wait(timeout=_STATUS_INTERVAL_S):
            if self._protocol is not None and self._protocol.is_connected():
                try:
                    status = self._build_status_payload()
                    self._protocol.send_status(status, priority=0)
                    logger.debug("Status published to server")
                except Exception as exc:
                    logger.warning("Status publish failed: %s", exc)
