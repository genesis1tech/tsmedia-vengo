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
        Switch to the idle/attract loop.

        Resolves MP4 paths from the tsv6_idle_loop playlist cache, hands
        them to the renderer, and starts impression tracking for each asset.
        """
        self._interrupt_current_idle()

        mp4_paths = self._resolve_idle_mp4s()
        if not mp4_paths and self._renderer is not None:
            logger.warning("show_idle: no MP4 assets found in idle loop playlist")
            return False

        if self._renderer is None:
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

    def show_processing(self) -> bool:
        """Switch to the 'Verifying...' screen."""
        self._interrupt_current_idle()
        if self._renderer is None:
            return False
        return self._renderer.show_processing()

    def show_deposit_item(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'Please Deposit Your Item' screen.

        ``playlist_override`` accepted for ``DisplayController`` parity; the native
        renderer has no per-call playlist switch concept, so it is ignored.
        """
        self._interrupt_current_idle()
        if self._renderer is None:
            return False
        return self._renderer.show_deposit_item()

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
        return self._renderer.show_product_display(
            image_path=image_arg,
            qr_url=qr_url,
            nfc_url=nfc_url,
            product_name=product_name,
            product_brand=product_brand,
            product_desc=product_desc,
        )

    def show_no_match(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'Unrecognized Barcode' screen.

        ``playlist_override`` accepted for ``DisplayController`` parity; ignored by
        the native renderer.
        """
        self._interrupt_current_idle()
        if self._renderer is None:
            return False
        return self._renderer.show_no_match()

    def show_barcode_not_qr(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'QR Code Detected — Use Barcode' error screen.

        ``playlist_override`` accepted for ``DisplayController`` parity; ignored by
        the native renderer.
        """
        self._interrupt_current_idle()
        if self._renderer is None:
            return False
        return self._renderer.show_barcode_not_qr()

    def show_no_item_detected(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'Item Not Detected' screen.

        ``playlist_override`` accepted for ``DisplayController`` parity; ignored by
        the native renderer.
        """
        self._interrupt_current_idle()
        if self._renderer is None:
            return False
        return self._renderer.show_no_item_detected()

    def show_offline(self) -> bool:
        """Switch to the offline fallback screen."""
        self._interrupt_current_idle()
        if self._renderer is None:
            return False
        return self._renderer.show_offline()

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

            # Cache playlist -> asset mappings from the config.
            playlists: list[dict] = config_obj.get("playlists", [])
            for playlist in playlists:
                name = playlist.get("name", "")
                pl_assets: list[str] = playlist.get("assets", [])
                if name:
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

        # Font family: server may send a curated dropdown value (fontFamily)
        # and/or a custom override (fontFamilyCustom) which wins when set.
        font_family = str(
            ticker.get("fontFamilyCustom") or ticker.get("fontFamily") or ""
        ).strip()
        try:
            font_size_pct = float(ticker.get("fontSizePct") or 0)
        except (TypeError, ValueError):
            font_size_pct = 0.0
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
            "font=%r, size_pct=%s, color=%r, bg=%r, bold=%s, italic=%s, weight=%s, css=%r)",
            text, enabled and bool(text), behavior in ("scroll", "slide"), speed,
            font_family, font_size_pct, color, background, bold, italic, font_weight,
            custom_css,
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

        Maps the playlist name to a renderer call.  Only ``tsv6_idle_loop``
        starts impression tracking; all other playlists are system states.
        """
        logger.info("TSV6NativeBackend: setplaylist -> %s", playlist_name)
        try:
            if playlist_name == _IDLE_PLAYLIST:
                self.show_idle()
            elif playlist_name == "tsv6_processing":
                self.show_processing()
            elif playlist_name == "tsv6_deposit_item":
                self.show_deposit_item()
            elif playlist_name == "tsv6_no_match":
                self.show_no_match()
            elif playlist_name == "tsv6_barcode_not_qr":
                self.show_barcode_not_qr()
            elif playlist_name == "tsv6_no_item_detected":
                self.show_no_item_detected()
            elif playlist_name == "tsv6_offline":
                self.show_offline()
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

    def _resolve_idle_mp4s(self) -> list[Path]:
        """
        Return the list of local MP4 paths for the idle loop playlist.

        Reads ``{cache_dir}/__tsv6_idle_loop.json`` if present, otherwise
        falls back to scanning the cache_dir for ``*.mp4`` files.
        """
        cached = self._load_playlist_cache(_IDLE_PLAYLIST)
        mp4_names: list[str] = (
            [f for f in cached if f.lower().endswith(".mp4")]
            if cached
            else []
        )

        if not mp4_names:
            # Fallback: scan for any MP4s in cache dir.
            if self._cache_dir.exists():
                mp4_names = sorted(
                    p.name for p in self._cache_dir.glob("*.mp4")
                )

        return [
            self._cache_dir / name
            for name in mp4_names
            if (self._cache_dir / name).exists()
        ]

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

        Returns None if the file does not exist or is unreadable.
        """
        target = self._cache_dir / f"__{playlist_name}.json"
        if not target.exists():
            return None
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return list(data) if isinstance(data, list) else None
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read playlist cache %s: %s", target, exc)
            return None

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
