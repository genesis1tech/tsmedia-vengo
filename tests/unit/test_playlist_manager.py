"""Unit tests for TSV6 playlist definitions."""

from __future__ import annotations

from tsv6.display.playlist_manager import PLAYLIST_DEFINITIONS, PlaylistManager


def test_no_match_playlist_uses_unrecognized_item_video() -> None:
    """No-match should render the MP4 flow, matching the QR-error playlist."""
    definition = PLAYLIST_DEFINITIONS["tsv6_no_match"]

    assert definition["asset_dir"] == "assets/videos"
    assert definition["assets"] == ["unrecognized-item-scanned.mp4"]


def test_no_match_asset_resolves_from_repo() -> None:
    """The seeded no-match asset must exist in the repository assets path."""
    manager = PlaylistManager(adapter=None)  # type: ignore[arg-type]

    resolved = manager._resolve_assets(PLAYLIST_DEFINITIONS["tsv6_no_match"])

    assert [path.name for path in resolved] == ["unrecognized-item-scanned.mp4"]
