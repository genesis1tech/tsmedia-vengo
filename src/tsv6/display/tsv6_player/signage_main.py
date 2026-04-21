#!/usr/bin/env python3
"""
Signage-only entry point for TSV6NativeBackend.

Runs the Chromium+VLC native player without any recycling hardware
(no barcode scanner, servo, AWS IoT, NFC, or ToF sensor). Use this on
displays that exist purely for ad playback.

Environment variables:
    PISIGNAGE_SERVER_URL    http://72.60.120.25:3000
    PISIGNAGE_USERNAME      pi
    PISIGNAGE_PASSWORD      pi
    PISIGNAGE_INSTALLATION  g1tech26
    PISIGNAGE_GROUP         default
    TSV6_APP_VERSION        e.g. 1.0.0-signage
"""

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from tsv6.display.tsv6_player.backend import TSV6NativeBackend

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> int:
    _configure_logging()

    server_url = os.environ.get("PISIGNAGE_SERVER_URL", "http://localhost:3000")
    username = os.environ.get("PISIGNAGE_USERNAME", "pi")
    password = os.environ.get("PISIGNAGE_PASSWORD", "pi")
    installation = os.environ.get("PISIGNAGE_INSTALLATION", "g1tech26")
    group = os.environ.get("PISIGNAGE_GROUP", "default")
    app_version = os.environ.get("TSV6_APP_VERSION", "1.0.0-signage")
    venue_id = os.environ.get("TSV6_VENUE_ID") or None

    cache_dir = Path.home() / ".local/share/tsv6/player-media"
    impression_dir = Path.home() / ".local/share/tsv6/impressions"
    cache_dir.mkdir(parents=True, exist_ok=True)
    impression_dir.mkdir(parents=True, exist_ok=True)

    project_root = Path(__file__).resolve().parents[4]
    layout_html = project_root / "pisignage" / "templates" / "layouts" / "custom_layout.html"

    if not layout_html.exists():
        logger.error("Layout HTML not found at %s", layout_html)
        return 1

    logger.info("TSV6 Signage-Only Player starting")
    logger.info("  Server: %s", server_url)
    logger.info("  Installation: %s, Group: %s", installation, group)
    logger.info("  Layout: %s", layout_html)
    logger.info("  Cache: %s", cache_dir)

    backend = TSV6NativeBackend(
        server_url=server_url,
        username=username,
        password=password,
        cache_dir=cache_dir,
        layout_html=layout_html,
        installation=installation,
        group_name=group,
        app_version=app_version,
        venue_id=venue_id,
        impression_output_dir=impression_dir,
    )

    shutdown_event = threading.Event()

    def _handle_signal(signum: int, _frame) -> None:
        logger.info("Received signal %d, shutting down", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        if not backend.connect():
            logger.error("Failed to connect to PiSignage server")
            return 2

        backend.start()
        backend.show_idle()
        logger.info("Signage player running. Ctrl-C to stop.")

        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=5.0)

    except Exception as e:
        logger.exception("Fatal error: %s", e)
        return 3
    finally:
        logger.info("Stopping backend...")
        try:
            backend.stop()
        except Exception as e:
            logger.warning("Error during shutdown: %s", e)
        try:
            backend.disconnect()
        except Exception as e:
            logger.warning("Error during disconnect: %s", e)

    logger.info("Signage player exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
