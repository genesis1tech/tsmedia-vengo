"""
Unit tests for ImageManager.load_image_for_display null-path handling.

V2 cold-path Lambda emits openDoor with productImage=null on the very first
scan of a brand-new product (the WebP conversion happens after the publish,
so the first scan has no image yet). The image loader must tolerate falsy
inputs and return None so the overlay can render a text-only product card
instead of crashing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tsv6.core.image_manager import ImageManager


@pytest.fixture()
def image_manager(tmp_path):
    return ImageManager(cache_dir=str(tmp_path / "cache"), max_cache_size_mb=1)


class TestLoadImageForDisplayNullPath:
    """V2 cold-path productImage=null handling — must return None, not raise."""

    def test_returns_none_for_none_path(self, image_manager):
        assert image_manager.load_image_for_display(None, (100, 100)) is None

    def test_returns_none_for_empty_string(self, image_manager):
        assert image_manager.load_image_for_display("", (100, 100)) is None

    def test_returns_none_for_empty_path(self, image_manager):
        # Path("") is also falsy via the `not image_path` guard.
        assert image_manager.load_image_for_display(Path(""), (100, 100)) is None

    def test_returns_none_for_nonexistent_path_does_not_crash(self, image_manager, tmp_path):
        # Truthy but unreadable path goes down the PIL-load branch and is
        # caught by the try/except, so the helper still returns None instead
        # of bubbling the exception up to the overlay code.
        missing = tmp_path / "does_not_exist.png"
        assert image_manager.load_image_for_display(missing, (100, 100)) is None
