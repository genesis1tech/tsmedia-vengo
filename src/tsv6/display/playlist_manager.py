#!/usr/bin/env python3
"""
Playlist manager for TSV6 PiSignage integration.

Handles creation and seeding of all TSV6 playlists on the remote
PiSignage server. Uploads HTML templates and event images as assets.
Called during first-boot setup or when playlists need to be refreshed.
"""

import logging
from pathlib import Path
from typing import Any

from tsv6.display.pisignage_adapter import PiSignageAdapter

logger = logging.getLogger(__name__)

# Project root — assets and event_images are relative to this
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# All TSV6 playlists and their default content
PLAYLIST_DEFINITIONS: dict[str, dict[str, Any]] = {
    "tsv6_idle_loop": {
        "description": "Default idle state — looping brand videos",
        "asset_dir": "assets/videos",
        "asset_pattern": "*.mp4",
        "layout": "1",
    },
    "tsv6_processing": {
        "description": "Barcode verification in progress",
        "assets": ["image_verify.jpg"],
        "asset_dir": "event_images",
        "layout": "1",
    },
    "tsv6_deposit_item": {
        "description": "Door open — waiting for item deposit",
        "assets": ["tsv6_deposit_item.html"],
        "asset_dir": "pisignage/templates",
        "layout": "1",
    },
    "tsv6_product_display": {
        "description": "Product image with QR code after successful deposit",
        "assets": ["tsv6_product_display.html"],
        "asset_dir": "pisignage/templates",
        "layout": "1",
    },
    "tsv6_no_match": {
        "description": "Barcode not recognised — cannot accept",
        "assets": ["unrecognized-item-scanned.mp4"],
        "asset_dir": "assets/videos",
        "layout": "1",
    },
    "tsv6_barcode_not_qr": {
        "description": "Scanned data was a QR code, not a barcode",
        "assets": ["barcode_not_qr.jpg"],
        "asset_dir": "event_images",
        "layout": "1",
    },
    "tsv6_no_item_detected": {
        "description": "Door opened but no item detected by sensor",
        "assets": ["tsv6_no_item_detected.html"],
        "asset_dir": "pisignage/templates",
        "layout": "1",
    },
    "tsv6_offline": {
        "description": "Fallback content when server is unreachable",
        "assets": ["g1tech.jpg"],
        "asset_dir": "event_images",
        "layout": "1",
    },
}

# Layout template to upload alongside playlists (de-brands the footer)
LAYOUT_TEMPLATE = {
    "filename": "custom_layout.html",
    "asset_dir": "pisignage/templates/layouts",
}


class PlaylistManager:
    """Manages TSV6 playlists on the remote PiSignage server."""

    def __init__(self, adapter: PiSignageAdapter):
        self._adapter = adapter

    def seed_all(self) -> dict[str, bool]:
        """
        Create all playlists and upload their assets.

        Returns a dict of playlist_name -> success (bool).
        """
        results: dict[str, bool] = {}
        existing_playlists = {p.get("name") for p in self._adapter.list_playlists()}
        existing_assets = {
            a.get("name", a) if isinstance(a, dict) else a
            for a in self._adapter.list_assets()
        }

        # Upload the de-branded layout template first (so playlists reference it)
        layout_path = PROJECT_ROOT / LAYOUT_TEMPLATE["asset_dir"] / LAYOUT_TEMPLATE["filename"]
        if layout_path.exists() and LAYOUT_TEMPLATE["filename"] not in existing_assets:
            if self._adapter.upload_asset(str(layout_path)):
                existing_assets.add(LAYOUT_TEMPLATE["filename"])
                logger.info("Uploaded custom layout template: %s", LAYOUT_TEMPLATE["filename"])
            else:
                logger.warning("Failed to upload layout template — footer will remain")

        # Ensure every existing group has the layout in its deploy list
        # so players actually download it on sync.
        for group in self._adapter.list_groups():
            group_id = group.get("_id")
            if group_id:
                self._adapter.ensure_group_has_asset(
                    group_id, LAYOUT_TEMPLATE["filename"]
                )

        for name, definition in PLAYLIST_DEFINITIONS.items():
            try:
                success = self._seed_playlist(
                    name, definition, existing_playlists, existing_assets
                )
                results[name] = success
                if success:
                    logger.info("Seeded playlist: %s", name)
                else:
                    logger.warning("Failed to seed playlist: %s", name)
            except Exception as e:
                logger.error("Error seeding playlist '%s': %s", name, e)
                results[name] = False

        return results

    def _seed_playlist(
        self,
        name: str,
        definition: dict[str, Any],
        existing_playlists: set[str | None],
        existing_assets: set[str],
    ) -> bool:
        """Seed a single playlist: upload assets, create playlist, assign assets."""

        # Step 1: Upload assets that don't already exist on the server
        asset_files = self._resolve_assets(definition)
        uploaded_asset_names: list[str] = []

        for file_path in asset_files:
            asset_name = file_path.name
            if asset_name not in existing_assets:
                if self._adapter.upload_asset(str(file_path)):
                    uploaded_asset_names.append(asset_name)
                    existing_assets.add(asset_name)
                else:
                    logger.warning("Failed to upload asset: %s", asset_name)
            else:
                uploaded_asset_names.append(asset_name)
                logger.debug("Asset already exists: %s", asset_name)

        # Step 2: Create playlist if it doesn't exist
        if name not in existing_playlists:
            if not self._adapter.create_playlist(name):
                return False

        # Step 3: Update playlist with assets
        playlist_assets: list[dict[str, Any]] = [
            {
                "filename": asset_name,
                "duration": 10,
                "selected": True,
                "option": {},
            }
            for asset_name in uploaded_asset_names
        ]

        return self._adapter.update_playlist(
            name,
            assets=playlist_assets,
            layout=definition.get("layout", "1"),
            template_name=LAYOUT_TEMPLATE["filename"],
        )

    def _resolve_assets(self, definition: dict[str, Any]) -> list[Path]:
        """Resolve asset file paths from a playlist definition.

        All resolved paths are validated to stay within PROJECT_ROOT to
        prevent path traversal.
        """
        asset_dir = PROJECT_ROOT / definition.get("asset_dir", ".")
        project_root_resolved = PROJECT_ROOT.resolve()
        result: list[Path] = []

        if "assets" in definition:
            for asset_name in definition["assets"]:
                path = (asset_dir / asset_name).resolve()
                if not str(path).startswith(str(project_root_resolved)):
                    logger.error("Path traversal blocked: %s", path)
                    continue
                if path.exists():
                    result.append(path)
                else:
                    logger.warning("Asset not found: %s", path)
        elif "asset_pattern" in definition:
            pattern = definition["asset_pattern"]
            for match in sorted(asset_dir.glob(pattern)):
                resolved = match.resolve()
                if str(resolved).startswith(str(project_root_resolved)):
                    result.append(resolved)
            if not result:
                logger.warning("No assets matching '%s' in %s", pattern, asset_dir)

        return result

    def ensure_playlists_exist(self) -> bool:
        """Quick check: create any missing playlists (no asset upload)."""
        existing = {p.get("name") for p in self._adapter.list_playlists()}
        all_ok = True
        for name in PLAYLIST_DEFINITIONS:
            if name not in existing:
                if not self._adapter.create_playlist(name):
                    all_ok = False
        return all_ok
