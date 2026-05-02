"""
Unit tests for Vengo integration in TSV6NativeBackend.

Covers:
  - _build_vengo_url() URL construction
  - show_idle() routing: Vengo vs VLC fallback
  - VLC fallback on Vengo URL build failure

All subsystems are mocked — no filesystem, network, or real threads.
"""

from __future__ import annotations

from pathlib import Path
import time
from unittest.mock import MagicMock, patch

import pytest

from tsv6.display.identity import PlayerIdentity
from tsv6.display.tsv6_player import backend as backend_module
from tsv6.display.tsv6_player.backend import TSV6NativeBackend


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_identity(player_name: str = "TS_1234ABCD") -> PlayerIdentity:
    return PlayerIdentity(
        cpu_serial="000000001234ABCD",
        device_id="1234ABCD",
        player_name=player_name,
        eth_mac="aa:bb:cc:dd:ee:ff",
        wlan_mac="11:22:33:44:55:66",
    )


def _make_backend(
    tmp_path: Path,
    identity: PlayerIdentity | None = None,
) -> TSV6NativeBackend:
    """Return a backend with identity pre-set (skips connect)."""
    backend = TSV6NativeBackend(
        server_url="http://test:3000",
        username="testuser",
        password="testpass",
        cache_dir=tmp_path / "cache",
        layout_html=tmp_path / "layout.html",
        installation="testinstall",
        group_name="testgroup",
        app_version="0.0.1",
        venue_id="venue-test",
        impression_output_dir=tmp_path / "impressions",
        identity_override=identity or _make_identity(),
    )
    # Simulate connect() having run — set identity directly.
    backend._identity = identity or _make_identity()
    return backend


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_renderer():
    m = MagicMock()
    m.start.return_value = True
    m.is_connected = True
    m.show_idle.return_value = True
    m.show_vengo_idle.return_value = True
    m.show_product_display.return_value = True
    m.get_metrics.return_value = {
        "state": "idle",
        "chromium_running": True,
        "vlc_playing": False,
        "main_rect": (0, 0, 800, 420),
        "router_url": "http://127.0.0.1:8765",
    }
    return m


@pytest.fixture
def mock_tracker():
    m = MagicMock()
    m.on_play_start.return_value = "test-impression-id"
    return m


@pytest.fixture
def vengo_backend(tmp_path: Path):
    """Backend with identity and mock subsystems wired."""
    backend = _make_backend(tmp_path)
    return backend


# ═══════════════════════════════════════════════════════════════════════════════
#  _build_vengo_url
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildVengoUrl:
    """Test _build_vengo_url() helper."""

    def test_builds_correct_url_format(self, vengo_backend: TSV6NativeBackend, monkeypatch):
        """URL format: https://vast.vengo.tv?organization_id=g1tech&ad_unit_id=TS_1234ABCD"""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "enabled", True)
        monkeypatch.setattr(_cfg.vengo, "organization_id", "g1tech")
        monkeypatch.setattr(_cfg.vengo, "web_player_base_url", "https://vast.vengo.tv")
        monkeypatch.setattr(_cfg.vengo, "no_ad_url", "")

        url = vengo_backend._build_vengo_url()

        assert url.startswith("https://vast.vengo.tv?")
        assert "organization_id=g1tech" in url
        assert "ad_unit_id=TS_1234ABCD" in url

    def test_appends_no_ad_url_when_configured(self, vengo_backend: TSV6NativeBackend, monkeypatch):
        """no_ad_url is URL-encoded and appended."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "no_ad_url", "https://example.com/fallback.mp4")
        monkeypatch.setattr(_cfg.vengo, "web_player_base_url", "https://vast.vengo.tv")
        monkeypatch.setattr(_cfg.vengo, "organization_id", "g1tech")

        url = vengo_backend._build_vengo_url()

        # URL-encoded: https%3A%2F%2Fexample.com%2Ffallback.mp4
        assert "no_ad_url=" in url
        assert "https%3A%2F%2Fexample.com%2Ffallback.mp4" in url

    def test_omits_no_ad_url_when_empty(self, vengo_backend: TSV6NativeBackend, monkeypatch):
        """No no_ad_url parameter when not configured."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "no_ad_url", "")
        monkeypatch.setattr(_cfg.vengo, "web_player_base_url", "https://vast.vengo.tv")
        monkeypatch.setattr(_cfg.vengo, "organization_id", "g1tech")

        url = vengo_backend._build_vengo_url()

        assert "no_ad_url" not in url

    def test_returns_empty_when_no_identity(self, vengo_backend: TSV6NativeBackend, monkeypatch):
        """Returns empty string when identity is not set."""
        vengo_backend._identity = None

        url = vengo_backend._build_vengo_url()

        assert url == ""

    def test_uses_player_name_as_ad_unit_id(self, tmp_path: Path, monkeypatch):
        """Ad unit ID matches PlayerIdentity.player_name (TS_<LAST8> pattern)."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "web_player_base_url", "https://vast.vengo.tv")
        monkeypatch.setattr(_cfg.vengo, "organization_id", "g1tech")
        monkeypatch.setattr(_cfg.vengo, "no_ad_url", "")

        identity = _make_identity(player_name="TS_ABCD1234")
        backend = _make_backend(tmp_path, identity=identity)

        url = backend._build_vengo_url()

        assert "ad_unit_id=TS_ABCD1234" in url

    def test_custom_base_url(self, vengo_backend: TSV6NativeBackend, monkeypatch):
        """Custom base URL from VENGO_WEB_PLAYER_BASE_URL is used."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "web_player_base_url", "https://custom.vengo.tv")
        monkeypatch.setattr(_cfg.vengo, "organization_id", "testorg")
        monkeypatch.setattr(_cfg.vengo, "no_ad_url", "")

        url = vengo_backend._build_vengo_url()

        assert url.startswith("https://custom.vengo.tv?")
        assert "organization_id=testorg" in url

    def test_special_chars_in_no_ad_url_are_encoded(self, vengo_backend: TSV6NativeBackend, monkeypatch):
        """Special characters in no_ad_url are properly percent-encoded."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "no_ad_url", "https://example.com/fallback?v=1&x=2")
        monkeypatch.setattr(_cfg.vengo, "web_player_base_url", "https://vast.vengo.tv")
        monkeypatch.setattr(_cfg.vengo, "organization_id", "g1tech")

        url = vengo_backend._build_vengo_url()

        # ? and & and = in the no_ad_url value must be encoded
        assert "%3F" in url or "%3f" in url  # ? encoded
        assert "%26" in url  # & encoded


# ═══════════════════════════════════════════════════════════════════════════════
#  show_idle() Vengo routing
# ═══════════════════════════════════════════════════════════════════════════════


class TestShowIdleVengo:
    """Test show_idle() Vengo routing."""

    def test_routes_to_vengo_when_enabled(
        self,
        vengo_backend: TSV6NativeBackend,
        mock_renderer: MagicMock,
        monkeypatch,
    ):
        """show_idle() calls renderer.show_vengo_idle() when config.vengo.enabled=True."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "enabled", True)
        monkeypatch.setattr(_cfg.vengo, "web_player_base_url", "https://vast.vengo.tv")
        monkeypatch.setattr(_cfg.vengo, "organization_id", "g1tech")
        monkeypatch.setattr(_cfg.vengo, "no_ad_url", "")

        vengo_backend._renderer = mock_renderer
        vengo_backend._tracker = MagicMock()

        result = vengo_backend.show_idle()

        assert result is True
        mock_renderer.show_vengo_idle.assert_called_once()
        call_args = mock_renderer.show_vengo_idle.call_args
        url = call_args[0][0]
        assert "vast.vengo.tv" in url
        assert "organization_id=g1tech" in url
        assert "ad_unit_id=TS_1234ABCD" in url

    def test_routes_to_vlc_when_disabled(
        self,
        vengo_backend: TSV6NativeBackend,
        mock_renderer: MagicMock,
        mock_tracker: MagicMock,
        tmp_path: Path,
        monkeypatch,
    ):
        """show_idle() calls renderer.show_idle() (VLC) when config.vengo.enabled=False."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "enabled", False)

        vengo_backend._renderer = mock_renderer
        vengo_backend._tracker = mock_tracker

        # Pre-populate cache with an MP4 for VLC idle to work.
        vengo_backend._cache_dir.mkdir(parents=True, exist_ok=True)
        mp4 = vengo_backend._cache_dir / "idle_ad.mp4"
        mp4.write_bytes(b"fake video")
        vengo_backend._write_playlist_cache("tsv6_idle_loop", ["idle_ad.mp4"])

        result = vengo_backend.show_idle()

        assert result is True
        mock_renderer.show_vengo_idle.assert_not_called()
        mock_renderer.show_idle.assert_called_once()

    def test_falls_back_to_vlc_on_url_failure(
        self,
        vengo_backend: TSV6NativeBackend,
        mock_renderer: MagicMock,
        mock_tracker: MagicMock,
        tmp_path: Path,
        monkeypatch,
    ):
        """Falls back to VLC idle when Vengo URL build returns empty."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "enabled", True)

        vengo_backend._renderer = mock_renderer
        vengo_backend._tracker = mock_tracker

        # Make _build_vengo_url return empty (identity is None)
        vengo_backend._identity = None

        # Pre-populate cache for VLC fallback
        vengo_backend._cache_dir.mkdir(parents=True, exist_ok=True)
        mp4 = vengo_backend._cache_dir / "fallback.mp4"
        mp4.write_bytes(b"fake video")
        vengo_backend._write_playlist_cache("tsv6_idle_loop", ["fallback.mp4"])

        result = vengo_backend.show_idle()

        assert result is True
        mock_renderer.show_vengo_idle.assert_not_called()
        mock_renderer.show_idle.assert_called_once()

    def test_vengo_show_vengo_idle_failure_falls_back_to_vlc(
        self,
        vengo_backend: TSV6NativeBackend,
        mock_renderer: MagicMock,
        mock_tracker: MagicMock,
        tmp_path: Path,
        monkeypatch,
    ):
        """Falls back to VLC when renderer.show_vengo_idle() returns False."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "enabled", True)
        monkeypatch.setattr(_cfg.vengo, "web_player_base_url", "https://vast.vengo.tv")
        monkeypatch.setattr(_cfg.vengo, "organization_id", "g1tech")
        monkeypatch.setattr(_cfg.vengo, "no_ad_url", "")

        mock_renderer.show_vengo_idle.return_value = False
        vengo_backend._renderer = mock_renderer
        vengo_backend._tracker = mock_tracker

        # Pre-populate cache for VLC fallback
        vengo_backend._cache_dir.mkdir(parents=True, exist_ok=True)
        mp4 = vengo_backend._cache_dir / "fallback.mp4"
        mp4.write_bytes(b"fake video")
        vengo_backend._write_playlist_cache("tsv6_idle_loop", ["fallback.mp4"])

        result = vengo_backend.show_idle()

        assert result is True
        mock_renderer.show_vengo_idle.assert_called_once()
        # Should have fallen back to VLC idle
        mock_renderer.show_idle.assert_called_once()

    def test_returns_false_when_no_renderer(self, vengo_backend: TSV6NativeBackend, monkeypatch):
        """Returns False when renderer is not set."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "enabled", True)

        vengo_backend._renderer = None

        result = vengo_backend.show_idle()

        assert result is False

    def test_no_impression_tracking_for_vengo_idle(
        self,
        vengo_backend: TSV6NativeBackend,
        mock_renderer: MagicMock,
        monkeypatch,
    ):
        """Vengo idle should NOT start VLC impression tracking."""
        from tsv6.config.config import config as _cfg
        monkeypatch.setattr(_cfg.vengo, "enabled", True)
        monkeypatch.setattr(_cfg.vengo, "web_player_base_url", "https://vast.vengo.tv")
        monkeypatch.setattr(_cfg.vengo, "organization_id", "g1tech")
        monkeypatch.setattr(_cfg.vengo, "no_ad_url", "")

        mock_tracker = MagicMock()
        vengo_backend._renderer = mock_renderer
        vengo_backend._tracker = mock_tracker

        vengo_backend.show_idle()

        # Vengo idle does not use the VLC impression tracker
        mock_tracker.on_play_start.assert_not_called()


class TestProtocolReconnectVengoRestart:
    """Display-server reconnect should refresh Vengo idle."""

    def test_protocol_reconnect_restarts_idle_when_started(
        self, vengo_backend: TSV6NativeBackend, mock_renderer: MagicMock, monkeypatch
    ):
        monkeypatch.setattr(backend_module.time, "sleep", lambda _delay: None)
        vengo_backend._started = True
        vengo_backend._renderer = mock_renderer
        vengo_backend.show_idle = MagicMock(return_value=True)

        vengo_backend._on_protocol_connect()

        assert _wait_for(lambda: vengo_backend.show_idle.called)

    def test_protocol_reconnect_does_not_interrupt_product(
        self, vengo_backend: TSV6NativeBackend, mock_renderer: MagicMock, monkeypatch
    ):
        monkeypatch.setattr(backend_module.time, "sleep", lambda _delay: None)
        mock_renderer.get_metrics.return_value = {"state": "product"}
        vengo_backend._started = True
        vengo_backend._renderer = mock_renderer
        vengo_backend.show_idle = MagicMock(return_value=True)

        vengo_backend._on_protocol_connect()

        time.sleep(0.05)
        vengo_backend.show_idle.assert_not_called()
