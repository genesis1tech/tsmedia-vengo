"""
Unit tests for Vengo methods in TSV6Renderer.

Covers:
  - show_vengo_idle(): SSE command, state transition
  - hide_vengo_idle(): SSE command
  - show_product_display() hides Vengo idle
  - show_offline() hides Vengo idle
  - play_video_loop() hides Vengo idle

All subsystems are mocked — no real display, VLC, or Chromium processes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tsv6.display.tsv6_player.renderer import TSV6Renderer
from tsv6.display.tsv6_player.router import RouterServer
from tsv6.display.tsv6_player.chromium import ChromiumKiosk
from tsv6.display.tsv6_player.vlc_zone import VLCZonePlayer


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def renderer(tmp_path: Path) -> TSV6Renderer:
    cache = tmp_path / "assets"
    cache.mkdir(exist_ok=True)
    layout = tmp_path / "router_page.html"
    layout.write_text("<html/>", encoding="utf-8")
    return TSV6Renderer(
        cache_dir=cache,
        layout_html=layout,
    )


@pytest.fixture
def mock_router(renderer: TSV6Renderer) -> MagicMock:
    mock = MagicMock(spec=RouterServer)
    mock.url = "http://127.0.0.1:8765/"
    mock.get_video_zone_rect.return_value = None
    renderer._router = mock
    return mock


@pytest.fixture
def mock_chromium(renderer: TSV6Renderer) -> MagicMock:
    mock = MagicMock(spec=ChromiumKiosk)
    mock.is_running.return_value = True
    mock.get_zone_rect.return_value = (0, 0, 800, 420)
    renderer._chromium = mock
    return mock


@pytest.fixture
def mock_vlc(renderer: TSV6Renderer) -> MagicMock:
    mock = MagicMock(spec=VLCZonePlayer)
    mock.is_playing.return_value = False
    mock.show.return_value = True
    renderer._vlc = mock
    return mock


def _find_command(mock_router: MagicMock, action: str) -> dict | None:
    """Find the first send_command call with the given action."""
    for call in mock_router.send_command.call_args_list:
        cmd = call[0][0]
        if cmd.get("action") == action:
            return cmd
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  show_vengo_idle
# ═══════════════════════════════════════════════════════════════════════════════


class TestShowVengoIdle:
    """Test show_vengo_idle() method."""

    def test_sends_show_vengo_idle_command(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ):
        """show_vengo_idle(url) sends correct SSE command."""
        url = "https://vast.vengo.tv?organization_id=g1tech&ad_unit_id=TS_1234ABCD"

        renderer.show_vengo_idle(url)

        cmd = _find_command(mock_router, "show_vengo_idle")
        assert cmd is not None
        assert cmd["url"] == url

    def test_sets_state_to_vengo_idle(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ):
        """State changes to 'vengo_idle'."""
        renderer.show_vengo_idle("https://vast.vengo.tv?org=1")

        assert renderer._state == "vengo_idle"

    def test_returns_true(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ):
        """Returns True on success."""
        result = renderer.show_vengo_idle("https://vast.vengo.tv?org=1")

        assert result is True

    def test_parks_vlc_when_active(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ):
        """Stops and lowers VLC playback before showing Vengo iframe."""
        mock_vlc.is_playing.return_value = True

        renderer.show_vengo_idle("https://vast.vengo.tv?org=1")

        mock_vlc.soft_stop.assert_called_once()
        mock_vlc.set_window_visible.assert_called_once_with(False)
        hide_cmd = _find_command(mock_router, "hide_video_zone")
        assert hide_cmd is not None

    def test_parks_vlc_even_after_playback_ended(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ):
        """A finished MP4 can still leave its final frame mapped over Chromium."""
        mock_vlc.is_playing.return_value = False

        renderer.show_vengo_idle("https://vast.vengo.tv?org=1")

        mock_vlc.soft_stop.assert_called_once()
        mock_vlc.set_window_visible.assert_called_once_with(False)
        hide_cmd = _find_command(mock_router, "hide_video_zone")
        assert hide_cmd is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  hide_vengo_idle
# ═══════════════════════════════════════════════════════════════════════════════


class TestHideVengoIdle:
    """Test hide_vengo_idle() method."""

    def test_sends_hide_vengo_idle_command(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ):
        """hide_vengo_idle() sends correct SSE command."""
        renderer.hide_vengo_idle()

        cmd = _find_command(mock_router, "hide_vengo_idle")
        assert cmd is not None

    def test_returns_true(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ):
        """Returns True."""
        result = renderer.hide_vengo_idle()

        assert result is True


# ═══════════════════════════════════════════════════════════════════════════════
#  show_product_display hides Vengo
# ═══════════════════════════════════════════════════════════════════════════════


class TestShowProductDisplayHidesVengo:
    """Verify show_product_display() hides the Vengo iframe."""

    def test_hides_vengo_idle(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
        tmp_path: Path,
    ):
        """show_product_display() sends hide_vengo_idle command."""
        image = tmp_path / "assets" / "product.jpg"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"fake image")

        renderer.show_product_display(image, "https://example.com/qr")

        cmd = _find_command(mock_router, "hide_vengo_idle")
        assert cmd is not None

    def test_show_product_display_with_url_image_hides_vengo(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ):
        """Product display with a remote URL image also hides Vengo."""
        renderer.show_product_display(
            "https://s3.example.com/product.webp",
            "https://example.com/qr",
        )

        cmd = _find_command(mock_router, "hide_vengo_idle")
        assert cmd is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  show_offline hides Vengo
# ═══════════════════════════════════════════════════════════════════════════════


class TestShowOfflineHidesVengo:
    """Verify show_offline() hides the Vengo iframe."""

    def test_hides_vengo_idle(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ):
        """show_offline() sends hide_vengo_idle command."""
        renderer.show_offline()

        cmd = _find_command(mock_router, "hide_vengo_idle")
        assert cmd is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  play_video_loop hides Vengo
# ═══════════════════════════════════════════════════════════════════════════════


class TestPlayVideoLoopHidesVengo:
    """Verify play_video_loop() hides the Vengo iframe."""

    def test_hides_vengo_idle(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
        tmp_path: Path,
    ):
        """play_video_loop() sends hide_vengo_idle before VLC starts."""
        mp4 = tmp_path / "video.mp4"
        mp4.write_bytes(b"fake video")

        renderer.play_video_loop([mp4], state="idle", loop=True)

        cmd = _find_command(mock_router, "hide_vengo_idle")
        assert cmd is not None
