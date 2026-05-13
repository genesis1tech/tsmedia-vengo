#!/usr/bin/env python3
"""
Signage-only entry point for TSV6NativeBackend.

Runs the Chromium+VLC native player without any recycling hardware
(no barcode scanner, servo, AWS IoT, NFC, or ToF sensor). Use this on
displays that exist purely for ad playback.

Startup order:
    1. Bring up SIM7600CE 4G LTE modem (default connectivity)
    2. Wait for data connection
    3. Connect to PiSignage server and start the player

Environment variables (PiSignage):
    PISIGNAGE_SERVER_URL    https://tsmedia.g1tech.cloud
    PISIGNAGE_USERNAME      pi
    PISIGNAGE_PASSWORD      pi
    PISIGNAGE_INSTALLATION  g1tech26
    PISIGNAGE_GROUP         default
    TSV6_APP_VERSION        e.g. 1.0.0-signage

Environment variables (LTE):
    TSV6_LTE_ENABLED            true|false (default: true for signage)
    TSV6_LTE_PORT               /dev/ttySIM7600
    TSV6_LTE_BAUD               115200
    TSV6_LTE_APN                APN string (set per SIM provider)
    TSV6_LTE_APN_USERNAME       (optional)
    TSV6_LTE_APN_PASSWORD       (optional)
    TSV6_LTE_FORCE_LTE          true|false (default: true)
    TSV6_LTE_ROAMING            true|false (default: true)
    TSV6_LTE_POWER_GPIO         BCM pin for modem power (default: 6)
    TSV6_LTE_CONNECT_TIMEOUT    seconds to wait for initial data link (default: 90)
    TSV6_LTE_REQUIRED           true|false — abort startup if LTE fails (default: false)
    TSV6_LTE_SIMULATION         true|false (default: false)
"""

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from tsv6.display.tsv6_player.backend import TSV6NativeBackend
from tsv6.display.tsv6_player.touch_gesture import LongPressWatcher

try:
    from tsv6.hardware.sim7600 import SIM7600Config, SIM7600Controller
    SIM7600_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - depends on hardware deps
    SIM7600_AVAILABLE = False
    _sim7600_import_error: Optional[BaseException] = exc
else:
    _sim7600_import_error = None

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _build_lte_config() -> SIM7600Config:
    """Build SIM7600 config from environment. Controller also reads env vars."""
    apn = os.environ.get("TSV6_LTE_APN", "hologram")
    connect_timeout = float(os.environ.get("TSV6_LTE_CONNECT_TIMEOUT", "90"))
    return SIM7600Config(
        apn=apn,
        apn_username=os.environ.get("TSV6_LTE_APN_USERNAME", ""),
        apn_password=os.environ.get("TSV6_LTE_APN_PASSWORD", ""),
        force_lte=_env_bool("TSV6_LTE_FORCE_LTE", True),
        enable_roaming=_env_bool("TSV6_LTE_ROAMING", True),
        rndis_mode=_env_bool("TSV6_LTE_RNDIS", True),
        connect_timeout=connect_timeout,
        simulation_mode=_env_bool("TSV6_LTE_SIMULATION", False),
    )


def _start_lte_modem() -> Optional["SIM7600Controller"]:
    """
    Bring up the SIM7600CE 4G modem before launching the player.

    Returns the connected controller, or None if LTE is disabled/unavailable.
    If LTE is required (TSV6_LTE_REQUIRED=true) and fails, raises RuntimeError.
    """
    if not _env_bool("TSV6_LTE_ENABLED", True):
        logger.info("LTE disabled via TSV6_LTE_ENABLED=false")
        return None

    if not SIM7600_AVAILABLE:
        logger.warning(
            "SIM7600 module unavailable (%s) — continuing without LTE",
            _sim7600_import_error,
        )
        return None

    logger.info("Starting SIM7600CE 4G LTE modem...")
    config = _build_lte_config()
    controller = SIM7600Controller(config=config)

    if controller.connect():
        status = controller.get_network_status()
        logger.info(
            "LTE up: operator=%s rssi=%s dBm ip=%s",
            status.get("operator", "?"),
            status.get("rssi_dbm", "?"),
            status.get("ip_address", "?"),
        )
        return controller

    if _env_bool("TSV6_LTE_REQUIRED", False):
        raise RuntimeError("LTE required but failed to connect")

    logger.warning("LTE modem failed to connect — continuing without LTE")
    try:
        controller.cleanup()
    except Exception:  # pragma: no cover - best effort
        pass
    return None


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

    layout_html = Path(__file__).resolve().parent / "router_page.html"

    if not layout_html.exists():
        logger.error("Layout HTML not found at %s", layout_html)
        return 1

    logger.info("TSV6 Signage-Only Player starting")
    logger.info("  Server: %s", server_url)
    logger.info("  Installation: %s, Group: %s", installation, group)
    logger.info("  Layout: %s", layout_html)
    logger.info("  Cache: %s", cache_dir)

    # Step 1: LTE modem up before anything that needs the network
    lte_controller: Optional["SIM7600Controller"] = None
    try:
        lte_controller = _start_lte_modem()
    except RuntimeError as e:
        logger.error("LTE startup failed: %s", e)
        return 4

    # Step 2: PiSignage backend
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

        # Long-press-anywhere-for-5s gesture → navigate kiosk to /settings.
        # Reads /dev/input/event1 (Goodix touchscreen) directly; bypasses
        # Chromium's flaky DOM touch dispatch on this DSI+X11 stack.
        #
        # VLC lives in a sibling Tk X11 window that sits ABOVE Chromium in the
        # stacking order (that is how video shows through the transparent
        # #main zone on the router page). XConfigureWindow(Above) is ignored
        # by the compositor on this Pi, so for settings visibility we UNMAP
        # the Tk window entirely. VLC keeps running underneath, invisible;
        # when the user exits settings we map the Tk window back and VLC
        # resumes visible playback.
        def _toggle_vlc_window(hide: bool) -> None:
            try:
                from Xlib import display as xdisplay
                d = xdisplay.Display()
                root = d.screen().root
                found = False
                def walk(w):
                    nonlocal found
                    try:
                        cls = w.get_wm_class()
                    except Exception:
                        cls = None
                    if cls and cls[0] == "tk":
                        if hide:
                            w.unmap()
                        else:
                            w.map()
                        d.sync()
                        found = True
                        return True
                    for c in w.query_tree().children:
                        if walk(c):
                            return True
                    return False
                walk(root)
                d.close()
                if found:
                    logger.info("VLC Tk window %s", "unmapped" if hide else "mapped")
                else:
                    logger.warning("VLC Tk window not found; skip %s",
                                   "unmap" if hide else "map")
            except Exception:
                logger.exception("Toggle VLC window failed")

        def _open_settings() -> None:
            _toggle_vlc_window(hide=True)
            try:
                import json, urllib.request, websocket
                pages = json.loads(
                    urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=2).read()
                )
                tgt = next(
                    (p for p in pages if p.get("type") == "page" and "8765" in p.get("url", "")),
                    None,
                )
                if not tgt:
                    logger.warning("Long-press: no kiosk page found via CDP")
                    return
                ws = websocket.create_connection(
                    tgt["webSocketDebuggerUrl"], timeout=2,
                    origin="http://localhost:9222",
                )
                ws.send(json.dumps({
                    "id": 1, "method": "Page.navigate",
                    "params": {"url": "http://127.0.0.1:8765/settings"},
                }))
                ws.recv()
                ws.close()
                logger.info("Long-press: navigated kiosk to /settings")
            except Exception:
                logger.exception("Long-press: CDP navigate failed")

        def _resume_idle() -> None:
            """Called when the user closes settings.

            Remap the legacy VLC window if present, then explicitly restart the
            idle display path. In Vengo mode this sends a fresh show_vengo_idle
            command to Chromium; without it, returning from /settings can leave
            the router page on the ready placeholder.
            """
            _toggle_vlc_window(hide=False)
            try:
                if not backend.show_idle():
                    logger.warning("Settings exit: backend.show_idle() returned false")
                else:
                    logger.info("Settings exit: idle display restarted")
            except Exception:
                logger.exception("Settings exit: failed to restart idle display")

        # Wire the wake callback into the router so POST /api/exit-settings
        # restarts idle playback when the user leaves the settings page.
        renderer = getattr(backend, "_renderer", None)
        router = getattr(renderer, "_router", None) if renderer else None
        if router is not None and hasattr(router, "set_wake_callback"):
            router.set_wake_callback(_resume_idle)

        long_press_hold = float(os.environ.get("TSV6_LONGPRESS_SECONDS", "5"))
        long_press = LongPressWatcher(_open_settings, hold_seconds=long_press_hold)
        long_press.start()

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

        if lte_controller is not None:
            logger.info("Shutting down LTE modem...")
            try:
                lte_controller.cleanup()
            except Exception as e:
                logger.warning("Error during LTE cleanup: %s", e)

    logger.info("Signage player exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
