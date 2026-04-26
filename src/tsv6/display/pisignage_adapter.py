#!/usr/bin/env python3
"""
PiSignage REST API adapter for TSV6 media playback.

Replaces the VLC-based EnhancedVideoPlayer with PiSignage-mediated playback.
All media rendering is delegated to the PiSignage player (Chromium kiosk on
the local Pi); this module drives playlist switching via the remote PiSignage
server REST API running on Hostinger.

Follows the same Manager pattern as ResilientAWSManager:
- Injected into ProductionVideoPlayer
- Registered with ErrorRecoverySystem
- Thread-safe playlist switching
- Retry with exponential backoff
"""

import logging
import os
import re
import time
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

from tsv6.display.controller import DisplayController  # noqa: F401 — satisfies Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PiSignageConfig:
    """PiSignage connection configuration. All secrets from environment."""

    server_url: str = field(
        default_factory=lambda: os.environ.get(
            "PISIGNAGE_SERVER_URL", "http://localhost:3000"
        )
    )
    username: str = field(
        default_factory=lambda: os.environ.get("PISIGNAGE_USERNAME", "pi")
    )
    password: str = field(
        default_factory=lambda: os.environ.get("PISIGNAGE_PASSWORD", "pi")
    )
    default_playlist: str = "tsv6_idle_loop"
    processing_playlist: str = "tsv6_processing"
    deposit_playlist: str = "tsv6_processing"
    product_playlist: str = "tsv6_product_display"
    no_match_playlist: str = "tsv6_no_match"
    barcode_not_qr_playlist: str = "tsv6_barcode_not_qr"
    no_item_playlist: str = "tsv6_no_item_detected"
    offline_playlist: str = "tsv6_offline"
    request_timeout: float = 5.0
    health_check_interval: float = 30.0
    max_retries: int = 3
    retry_base_delay: float = 0.5
    max_asset_size_mb: int = 100


class PiSignageAdapter:
    """
    Drives PiSignage player via the remote server REST API.

    The server runs on Hostinger VPS. The player runs locally on the Pi.
    This adapter sends REST calls to the server, which pushes config to the
    player via Socket.IO/WebSocket.

    Thread-safe: all mutable state is protected by ``_lock``.
    """

    def __init__(
        self,
        config: PiSignageConfig | None = None,
        on_connection_change: Callable[[bool], None] | None = None,
    ):
        self._config = config or PiSignageConfig()
        self._auth = HTTPBasicAuth(self._config.username, self._config.password)
        self._base = self._config.server_url.rstrip("/")
        self._on_connection_change = on_connection_change

        # All mutable state guarded by _lock
        self._lock = threading.Lock()
        self._player_id: str | None = None
        self._player_cpu_serial: str | None = None
        self._current_playlist: str | None = None
        self._connected: bool = False
        self._last_switch_latency_ms: float = 0.0
        self._total_switches: int = 0
        self._failed_switches: int = 0

    # ── Lifecycle ────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Discover the Pi player registered on the remote server."""
        try:
            resp = self._get("/api/players")
            players = resp.get("data", {})
            # Handle both list and paginated response formats
            if isinstance(players, list):
                player_list = players
            elif isinstance(players, dict):
                player_list = players.get("objects", players.get("data", []))
            else:
                player_list = []

            if not player_list:
                logger.warning("No PiSignage players registered on server")
                return False

            # Use the first player (single-player deployment)
            player = player_list[0]
            with self._lock:
                self._player_id = player.get("_id")
                self._player_cpu_serial = player.get("cpuSerialNumber", "unknown")
                self._connected = True

            if self._on_connection_change:
                self._on_connection_change(True)

            logger.info(
                "PiSignage connected: player_id=%s cpu=%s server=%s",
                self._player_id,
                self._player_cpu_serial,
                self._base,
            )
            return True

        except Exception as e:
            logger.error("PiSignage connection failed: %s", e)
            with self._lock:
                self._connected = False
            if self._on_connection_change:
                self._on_connection_change(False)
            return False

    def disconnect(self) -> None:
        """Mark adapter as disconnected."""
        with self._lock:
            self._connected = False
            self._player_id = None
        if self._on_connection_change:
            self._on_connection_change(False)
        logger.info("PiSignage adapter disconnected")

    def start(self) -> None:
        """Start background services (no-op at this layer; subclasses may override).

        Health monitor background threads are started by PiSignageHealthMonitor
        when composed externally. This method exists to satisfy the
        DisplayController lifecycle protocol.
        """
        logger.debug("PiSignageAdapter.start() called (no background threads here)")

    def stop(self) -> None:
        """Stop background services and disconnect.

        Calls disconnect() to release the server-side session and clear
        state. Background threads owned by external monitors must be stopped
        by their owners.
        """
        logger.debug("PiSignageAdapter.stop() called")
        self.disconnect()

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected and self._player_id is not None

    @property
    def player_id(self) -> str | None:
        with self._lock:
            return self._player_id

    @property
    def server_url(self) -> str:
        return self._base

    # ── Playlist Control ─────────────────────────────────────────────────

    def switch_playlist(self, playlist_name: str) -> bool:
        """Switch the player to a named playlist with retry.

        Retries release the lock during backoff sleep so that concurrent
        callers are not blocked.
        """
        with self._lock:
            player_id = self._player_id
        if not player_id:
            logger.warning("Cannot switch playlist: no player discovered")
            return False

        start = time.monotonic()
        for attempt in range(1, self._config.max_retries + 1):
            try:
                self._post(
                    f"/api/setplaylist/{player_id}/{playlist_name}"
                )
                elapsed_ms = (time.monotonic() - start) * 1000
                with self._lock:
                    self._last_switch_latency_ms = elapsed_ms
                    self._total_switches += 1
                    self._current_playlist = playlist_name
                logger.info(
                    "Playlist switched to '%s' (%.0fms, attempt %d)",
                    playlist_name,
                    elapsed_ms,
                    attempt,
                )
                return True
            except (requests.ConnectionError, requests.Timeout) as e:
                delay = self._config.retry_base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Playlist switch attempt %d/%d failed: %s (retry in %.1fs)",
                    attempt,
                    self._config.max_retries,
                    e,
                    delay,
                )
                if attempt < self._config.max_retries:
                    time.sleep(delay)
            except requests.HTTPError as e:
                logger.error("Playlist switch HTTP error: %s", e)
                break  # Don't retry on 4xx errors

        with self._lock:
            self._failed_switches += 1
        logger.error(
            "Failed to switch playlist to '%s' after %d attempts",
            playlist_name,
            self._config.max_retries,
        )
        return False

    _VALID_PLAYLIST_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,64}")

    def _resolve_playlist(self, override: str | None, default: str) -> str:
        """Validate an AWS-supplied playlist name; fall back to ``default`` if absent or unsafe."""
        if not override or not isinstance(override, str):
            return default
        if not self._VALID_PLAYLIST_NAME.fullmatch(override):
            logger.warning(
                "invalid playlist name %r — falling back to %s", override, default
            )
            return default
        return override

    def set_default_playlist(self) -> bool:
        """Return to the idle video loop."""
        return self.switch_playlist(self._config.default_playlist)

    def show_processing(self) -> bool:
        """Show the 'Verifying...' screen."""
        return self.switch_playlist(self._config.processing_playlist)

    def show_deposit_item(self, playlist_override: str | None = None) -> bool:
        """Switch to the 'Please Deposit Your Item' screen.

        Args:
            playlist_override: Optional AWS-supplied playlist name for per-campaign
                messaging during the deposit stage. Falls back to
                ``self._config.deposit_playlist`` when absent or invalid.
        """
        name = self._resolve_playlist(playlist_override, self._config.deposit_playlist)
        return self.switch_playlist(name)

    def show_idle(self) -> bool:
        """Switch to the default looping state. Alias for set_default_playlist()."""
        return self.set_default_playlist()

    def show_product_display(
        self,
        product_image_path: str = "",
        qr_url: str = "",
        nfc_url: str | None = None,
        playlist_override: str | None = None,
    ) -> bool:
        """Switch to the product result playlist.

        Args:
            product_image_path: Reserved for native-backend renderers; ignored here.
            qr_url: Reserved for native-backend renderers; ignored here. The QR is
                rendered Pi-side by ``QrOverlay`` when this adapter is the active
                display backend.
            nfc_url: Reserved for native-backend renderers; ignored here.
            playlist_override: Optional AWS-supplied playlist name for per-campaign
                reward content. Falls back to ``self._config.product_playlist`` when
                absent or invalid.
        """
        name = self._resolve_playlist(playlist_override, self._config.product_playlist)
        return self.switch_playlist(name)

    def show_offline(self) -> bool:
        """Show the offline / server-unreachable fallback screen."""
        return self.switch_playlist(self._config.offline_playlist)

    def show_no_match(self, playlist_override: str | None = None) -> bool:
        """Show the 'Cannot Accept' screen.

        Args:
            playlist_override: Optional AWS-supplied playlist name for per-campaign
                no-match messaging. Falls back to ``self._config.no_match_playlist``
                when absent or invalid.
        """
        name = self._resolve_playlist(playlist_override, self._config.no_match_playlist)
        return self.switch_playlist(name)

    def show_barcode_not_qr(self, playlist_override: str | None = None) -> bool:
        """Show the 'Barcode Not QR' screen.

        Args:
            playlist_override: Optional AWS-supplied playlist name for per-campaign
                QR-warning messaging. Falls back to
                ``self._config.barcode_not_qr_playlist`` when absent or invalid.
        """
        name = self._resolve_playlist(
            playlist_override, self._config.barcode_not_qr_playlist
        )
        return self.switch_playlist(name)

    def show_no_item_detected(self, playlist_override: str | None = None) -> bool:
        """Show the 'Item Not Detected' screen.

        Args:
            playlist_override: Optional AWS-supplied playlist name for per-campaign
                no-item messaging. Falls back to ``self._config.no_item_playlist``
                when absent or invalid.
        """
        name = self._resolve_playlist(playlist_override, self._config.no_item_playlist)
        return self.switch_playlist(name)

    # ── Asset Management ─────────────────────────────────────────────────

    def upload_asset(self, file_path: str, timeout: float = 60.0) -> bool:
        """Upload a media file to the remote PiSignage server."""
        path = Path(file_path)
        if not path.exists():
            logger.error("Asset file not found: %s", file_path)
            return False

        # Guard against oversized uploads
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > self._config.max_asset_size_mb:
            logger.error(
                "Asset too large: %s is %.1fMB (limit %dMB)",
                path.name, size_mb, self._config.max_asset_size_mb,
            )
            return False

        try:
            with open(file_path, "rb") as f:
                files = {"assets": (path.name, f)}
                resp = requests.post(
                    f"{self._base}/api/files",
                    files=files,
                    auth=self._auth,
                    timeout=timeout,
                )
            resp.raise_for_status()
            logger.info("Uploaded asset: %s (%.1fMB)", path.name, size_mb)
            return True
        except Exception as e:
            logger.error("Asset upload failed for %s: %s", path.name, e)
            return False

    def list_assets(self) -> list[dict]:
        """List all assets on the remote server."""
        try:
            resp = self._get("/api/files")
            return resp.get("data", [])
        except Exception as e:
            logger.error("Failed to list assets: %s", e)
            return []

    # ── Playlist Management ──────────────────────────────────────────────

    def create_playlist(self, name: str) -> bool:
        """Create a new playlist on the server."""
        try:
            self._post("/api/playlists", json_data={"file": name})
            logger.info("Created playlist: %s", name)
            return True
        except requests.HTTPError as e:
            # 409 or similar if playlist already exists — not an error
            if e.response is not None and e.response.status_code in (409, 400):
                logger.debug("Playlist '%s' may already exist: %s", name, e)
                return True
            logger.error("Failed to create playlist '%s': %s", name, e)
            return False
        except Exception as e:
            logger.error("Failed to create playlist '%s': %s", name, e)
            return False

    def update_playlist(
        self,
        name: str,
        assets: list[dict],
        layout: str = "1",
        template_name: str | None = None,
    ) -> bool:
        """Update playlist content with asset list."""
        try:
            payload: dict = {
                "assets": assets,
                "layout": layout,
                "settings": {
                    "ticker": {"enable": False},
                    "ads": {"adPlaylist": False},
                    "audio": {"enable": False},
                },
            }
            if template_name:
                payload["templateName"] = template_name
            self._post(f"/api/playlists/{name}", json_data=payload)
            logger.info("Updated playlist '%s' with %d assets", name, len(assets))
            return True
        except Exception as e:
            logger.error("Failed to update playlist '%s': %s", name, e)
            return False

    def list_playlists(self) -> list[dict]:
        """List all playlists on the server."""
        try:
            resp = self._get("/api/playlists")
            return resp.get("data", [])
        except Exception as e:
            logger.error("Failed to list playlists: %s", e)
            return []

    # ── Group / Deploy ───────────────────────────────────────────────────

    def deploy_to_group(self, group_id: str) -> bool:
        """Trigger deploy (sync assets) to a player group."""
        try:
            self._post(f"/api/groups/{group_id}", json_data={"deploy": True})
            logger.info("Deploy triggered for group %s", group_id)
            return True
        except Exception as e:
            logger.error("Deploy failed for group %s: %s", group_id, e)
            return False

    def ensure_group_has_asset(self, group_id: str, asset_filename: str) -> bool:
        """Ensure a specific asset (e.g. custom_layout.html) is in the group's
        deploy list so the player downloads it on sync."""
        try:
            resp = self._get(f"/api/groups/{group_id}")
            group = resp.get("data", {})
            assets = list(group.get("assets") or [])
            if asset_filename in assets:
                return True
            assets.append(asset_filename)
            self._post(
                f"/api/groups/{group_id}",
                json_data={"assets": assets, "deploy": True},
            )
            logger.info(
                "Added '%s' to group %s assets and deployed",
                asset_filename,
                group_id,
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to add '%s' to group %s: %s", asset_filename, group_id, e
            )
            return False

    def list_groups(self) -> list[dict]:
        """List all player groups."""
        try:
            resp = self._get("/api/groups")
            data = resp.get("data", {})
            if isinstance(data, dict):
                return data.get("objects", [])
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error("Failed to list groups: %s", e)
            return []

    # ── Health & Metrics ─────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Verify PiSignage server is responsive."""
        try:
            resp = self._get("/api/settings")
            healthy = resp.get("success", False)
        except Exception:
            healthy = False

        # Update connection state under lock
        with self._lock:
            was_connected = self._connected
            self._connected = healthy

        # Fire callback outside the lock to avoid deadlocks
        if healthy and not was_connected:
            if self._on_connection_change:
                self._on_connection_change(True)
        elif not healthy and was_connected:
            if self._on_connection_change:
                self._on_connection_change(False)

        return healthy

    def get_player_status(self) -> dict | None:
        """Get detailed player status from the server."""
        with self._lock:
            player_id = self._player_id
        if not player_id:
            return None
        try:
            resp = self._get(f"/api/players/{player_id}")
            return resp.get("data")
        except Exception as e:
            logger.error("Failed to get player status: %s", e)
            return None

    def get_metrics(self) -> dict:
        """Return adapter metrics for health monitoring."""
        with self._lock:
            return {
                "pisignage_connected": self._connected,
                "pisignage_server": self._base,
                "pisignage_player_id": self._player_id,
                "pisignage_current_playlist": self._current_playlist,
                "pisignage_last_switch_latency_ms": round(
                    self._last_switch_latency_ms, 1
                ),
                "pisignage_total_switches": self._total_switches,
                "pisignage_failed_switches": self._failed_switches,
            }

    # ── HTTP Helpers ─────────────────────────────────────────────────────

    def _get(self, path: str) -> dict:
        r = requests.get(
            f"{self._base}{path}",
            auth=self._auth,
            timeout=self._config.request_timeout,
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json_data: dict | None = None) -> dict:
        r = requests.post(
            f"{self._base}{path}",
            json=json_data,
            auth=self._auth,
            timeout=self._config.request_timeout,
        )
        r.raise_for_status()
        return r.json()
