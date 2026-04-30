"""Tests for VengoConfig dataclass."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

import os

import pytest

from tsv6.config.config import Config, VengoConfig


class TestVengoConfigDefaults:
    """Test default values when no env vars are set."""

    def test_default_enabled(self):
        cfg = VengoConfig()
        assert cfg.enabled is True

    def test_default_organization_id(self):
        cfg = VengoConfig()
        assert cfg.organization_id == "g1tech"

    def test_default_web_player_base_url(self):
        cfg = VengoConfig()
        assert cfg.web_player_base_url == "https://vast.vengo.tv"

    def test_default_no_ad_url(self):
        cfg = VengoConfig()
        assert cfg.no_ad_url == ""

    def test_default_ad_slot_duration_secs(self):
        cfg = VengoConfig()
        assert cfg.ad_slot_duration_secs == 30

    def test_default_fallback_to_pisignage(self):
        cfg = VengoConfig()
        assert cfg.fallback_to_pisignage is True


class TestVengoConfigEnvOverrides:
    """Test environment variable overrides."""

    def test_enabled_false(self, monkeypatch):
        monkeypatch.setenv("VENGO_ENABLED", "false")
        cfg = VengoConfig()
        assert cfg.enabled is False

    def test_enabled_zero(self, monkeypatch):
        monkeypatch.setenv("VENGO_ENABLED", "0")
        cfg = VengoConfig()
        assert cfg.enabled is False

    def test_enabled_yes(self, monkeypatch):
        monkeypatch.setenv("VENGO_ENABLED", "yes")
        cfg = VengoConfig()
        assert cfg.enabled is True

    def test_enabled_one(self, monkeypatch):
        monkeypatch.setenv("VENGO_ENABLED", "1")
        cfg = VengoConfig()
        assert cfg.enabled is True

    def test_organization_id_override(self, monkeypatch):
        monkeypatch.setenv("VENGO_ORGANIZATION_ID", "acme-corp")
        cfg = VengoConfig()
        assert cfg.organization_id == "acme-corp"

    def test_web_player_base_url_override(self, monkeypatch):
        monkeypatch.setenv("VENGO_WEB_PLAYER_BASE_URL", "https://custom.vengo.tv")
        cfg = VengoConfig()
        assert cfg.web_player_base_url == "https://custom.vengo.tv"

    def test_no_ad_url_override(self, monkeypatch):
        monkeypatch.setenv("VENGO_NO_AD_URL", "https://example.com/noad")
        cfg = VengoConfig()
        assert cfg.no_ad_url == "https://example.com/noad"


class TestConfigIntegration:
    """Test VengoConfig wired into Config class."""

    def test_config_has_vengo_attribute(self):
        cfg = Config()
        assert hasattr(cfg, "vengo")

    def test_config_vengo_is_vengo_config(self):
        cfg = Config()
        assert isinstance(cfg.vengo, VengoConfig)

    def test_config_vengo_defaults(self):
        cfg = Config()
        assert cfg.vengo.organization_id == "g1tech"
        assert cfg.vengo.enabled is True
        assert cfg.vengo.ad_slot_duration_secs == 30


class TestVengoConfigImport:
    """Test that VengoConfig can be imported."""

    def test_import_from_config_module(self):
        from tsv6.config.config import VengoConfig as VC
        assert VC is VengoConfig

    def test_in_all_exports(self):
        from tsv6.config import config as mod
        assert "VengoConfig" in mod.__all__
