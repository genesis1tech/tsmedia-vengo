"""
Ad player configuration dataclass.

All settings are sourced from environment variables with safe defaults.
TSV6_AD_ENABLED=0 by default — the entire module is a no-op until enabled.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DisplayAreaConfig:
    """Physical display constraints sent to the ad server."""

    width: int = 1280
    height: int = 800
    min_duration: int = 10  # seconds
    max_duration: int = 30  # seconds
    allow_audio: bool = False


@dataclass(frozen=True)
class AdConfig:
    """
    Runtime configuration for tsv6.ads.

    Construct via :func:`AdConfig.from_env` rather than calling directly.
    """

    # --- Server ---
    endpoint: str = ""
    """Base URL of the ts-ssp API, e.g. https://api.tsssp.com"""

    network_id: str = "topperstopper"
    """Network identifier sent in every ad request."""

    device_id: str = ""
    """Derived from DeviceConfig.thing_name at startup; injected by AdPlayer."""

    api_key: str = ""
    """Value of the X-Device-Key header.  Read from TSV6_AD_API_KEY."""

    enabled: bool = False
    """Master on/off switch.  Off by default — explicitly set TSV6_AD_ENABLED=1."""

    # --- Local storage ---
    cache_dir: str = "/var/lib/tsv6/ads"
    cache_max_bytes: int = 2_000_000_000  # 2 GB
    offline_db_path: str = "/var/lib/tsv6/ads/impressions.db"
    offline_max_rows: int = 10_000

    # --- Scheduling ---
    prefetch_lead_seconds: int = 60
    """Fetch the next ad pod this many seconds before the current ad ends."""

    # --- Display ---
    display_area: DisplayAreaConfig = field(default_factory=DisplayAreaConfig)

    @classmethod
    def from_env(cls, device_id: str = "") -> "AdConfig":
        """Build an AdConfig from environment variables."""
        raw_enabled = os.getenv("TSV6_AD_ENABLED", "0").strip()
        enabled = raw_enabled in ("1", "true", "yes", "on")

        raw_width = int(os.getenv("TSV6_AD_DISPLAY_WIDTH", "1280"))
        raw_height = int(os.getenv("TSV6_AD_DISPLAY_HEIGHT", "800"))
        raw_min_dur = int(os.getenv("TSV6_AD_MIN_DURATION", "10"))
        raw_max_dur = int(os.getenv("TSV6_AD_MAX_DURATION", "30"))

        display_area = DisplayAreaConfig(
            width=raw_width,
            height=raw_height,
            min_duration=raw_min_dur,
            max_duration=raw_max_dur,
            allow_audio=False,
        )

        return cls(
            endpoint=os.getenv("TSV6_AD_ENDPOINT", "").rstrip("/"),
            network_id=os.getenv("TSV6_AD_NETWORK_ID", "topperstopper"),
            device_id=device_id,
            api_key=os.getenv("TSV6_AD_API_KEY", ""),
            enabled=enabled,
            cache_dir=os.getenv("TSV6_AD_CACHE_DIR", "/var/lib/tsv6/ads"),
            cache_max_bytes=int(
                os.getenv("TSV6_AD_CACHE_MAX_BYTES", str(2_000_000_000))
            ),
            offline_db_path=os.getenv(
                "TSV6_AD_OFFLINE_DB",
                "/var/lib/tsv6/ads/impressions.db",
            ),
            offline_max_rows=int(os.getenv("TSV6_AD_OFFLINE_MAX_ROWS", "10000")),
            prefetch_lead_seconds=int(
                os.getenv("TSV6_AD_PREFETCH_LEAD_SECONDS", "60")
            ),
            display_area=display_area,
        )
