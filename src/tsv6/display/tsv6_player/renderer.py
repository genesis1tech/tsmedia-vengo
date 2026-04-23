"""
TSV6Renderer — top-level orchestrator for the Chromium-based display pipeline.

Composes three subsystems:

1. ``RouterServer``   — Flask server that serves the layout page and streams
                        SSE commands to the browser.
2. ``ChromiumKiosk``  — Launches and manages the Chromium kiosk process;
                        provides CDP helpers.
3. ``VLCZonePlayer``  — Plays video files in a Tk/X11 window that is
                        positioned behind the transparent Chromium zone.

Caller contract
---------------
Call ``start()`` once at application boot, then call the ``show_*`` methods
to transition between display states.  Call ``stop()`` on shutdown.

This class does NOT implement the ``DisplayController`` protocol; that
adaptation is handled separately by Agent D.

State model
-----------
The renderer tracks a coarse ``_state`` string (``"idle"``, ``"processing"``,
etc.) so it can decide whether to stop VLC before switching to a non-video
state.  No formal state machine is used to keep the implementation simple.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from tsv6.display.tsv6_player.chromium import ChromiumKiosk
from tsv6.display.tsv6_player.router import RouterServer
from tsv6.display.tsv6_player.vlc_zone import VLCZonePlayer

logger = logging.getLogger(__name__)

_DEFAULT_MAIN_RECT: tuple[int, int, int, int] = (0, 0, 800, 1220)

# How long to wait (seconds) after Chromium starts before querying the zone
# rect via CDP.
_RECT_QUERY_DELAY: float = 2.0


class TSV6Renderer:
    """
    Orchestrates the RouterServer, ChromiumKiosk, and VLCZonePlayer.

    Parameters
    ----------
    cache_dir:
        Directory where downloaded media assets are stored.
    layout_html:
        Absolute path to the router page HTML file.
    router_host:
        Bind address for the RouterServer.  Default ``"127.0.0.1"``.
    router_port:
        TCP port for the RouterServer.  Default ``8765``.
    display:
        X11 display string.  Default ``":0"``.
    xauthority:
        Path to the ``.Xauthority`` file.
    chromium_user_data_dir:
        Chromium profile directory.
    cdp_port:
        Chromium remote debugging port.
    width:
        Display width.
    height:
        Display height.
    vlc_args:
        Override VLC instance args.  ``None`` uses the Pi 5 defaults.
    """

    def __init__(
        self,
        cache_dir: Path,
        layout_html: Path,
        router_host: str = "127.0.0.1",
        router_port: int = 8765,
        display: str = ":0",
        xauthority: str = "/home/pi/.Xauthority",
        chromium_user_data_dir: Path = Path("/home/pi/.config/tsv6-chromium"),
        cdp_port: int = 9222,
        width: int = 800,
        height: int = 1280,
        vlc_args: list[str] | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._layout_html = layout_html
        self._width = width
        self._height = height

        self._router = RouterServer(
            cache_dir=cache_dir,
            layout_html=layout_html,
            host=router_host,
            port=router_port,
        )
        self._chromium = ChromiumKiosk(
            url=self._router.url,
            display=display,
            xauthority=xauthority,
            user_data_dir=chromium_user_data_dir,
            cdp_port=cdp_port,
            width=width,
            height=height,
        )
        self._vlc = VLCZonePlayer(vlc_args=vlc_args)

        self._main_rect: tuple[int, int, int, int] = _DEFAULT_MAIN_RECT
        self._state: str = "uninitialised"
        self._started: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> bool:
        """
        Start the RouterServer, launch Chromium, and measure the zone rect.

        Returns ``True`` if all subsystems initialised successfully.
        """
        logger.info("TSV6Renderer.start() — initialising subsystems.")

        # 1. Start the local Flask server first so Chromium has something to
        #    load immediately.
        self._router.start()

        # 2. Launch Chromium.
        if not self._chromium.start():
            logger.error("Chromium failed to start.")
            self._router.stop()
            return False

        # 3. Give the page a moment to render, then query the #main rect.
        time.sleep(_RECT_QUERY_DELAY)
        self._refresh_main_rect()

        self._started = True
        self._state = "idle"
        logger.info(
            "TSV6Renderer ready. #main rect=%s, url=%s",
            self._main_rect,
            self._router.url,
        )
        return True

    def stop(self) -> None:
        """Shut down VLC, Chromium, and the router server."""
        logger.info("TSV6Renderer.stop() called.")
        self._vlc.hide()
        self._chromium.stop()
        self._router.stop()
        self._started = False
        self._state = "stopped"

    # ── Display states ─────────────────────────────────────────────────────

    def show_idle(self, mp4_paths: list[Path]) -> bool:
        """
        Play idle loop videos in the ``#main`` zone.

        Sends ``show_video_zone`` to Chromium (making ``#main`` transparent),
        then starts VLC with the supplied MP4 files.

        Parameters
        ----------
        mp4_paths:
            List of MP4 files to loop.  Must be non-empty.

        Returns ``True`` on success.
        """
        if not mp4_paths:
            logger.warning("show_idle: no mp4_paths provided.")
            return False
        self._stop_vlc_if_active()
        self._router.send_command(
            {"action": "show_video_zone", "zone": "main", "rect": list(self._main_rect)}
        )
        # Short delay for the browser to act on the command.
        time.sleep(0.1)
        self._refresh_main_rect()
        ok = self._vlc.show(self._main_rect, mp4_paths, loop=True)
        if ok:
            self._state = "idle"
        return ok

    def show_processing(self) -> bool:
        """
        Display the processing screen.

        Stops VLC, then sends ``show_html`` pointing at the processing asset.

        Returns ``True`` always (SSE send is fire-and-forget).
        """
        self._stop_vlc_if_active()
        self._router.send_command(
            {"action": "show_html", "src": "tsv6_processing.html"}
        )
        self._state = "processing"
        return True

    def show_deposit_item(self) -> bool:
        """
        Display the "Please Deposit Your Item" screen.

        Returns ``True`` always.
        """
        self._stop_vlc_if_active()
        self._router.send_command(
            {"action": "show_html", "src": "tsv6_deposit_item.html"}
        )
        self._state = "deposit_item"
        return True

    def show_product_display(
        self,
        image_path: Path,
        qr_url: str,
        nfc_url: str | None = None,
    ) -> bool:
        """
        Display a product image with a QR-code overlay.

        Parameters
        ----------
        image_path:
            Local path to the product image (must be inside ``cache_dir``).
        qr_url:
            URL to encode in the QR code.
        nfc_url:
            Ignored by the renderer; provided for API symmetry with the
            DisplayController interface.

        Returns ``True`` always.
        """
        self._stop_vlc_if_active()
        self._router.send_command(
            {
                "action": "show_product",
                "image": image_path.name,
                "qr_url": qr_url,
            }
        )
        self._state = "product"
        return True

    def show_no_match(self) -> bool:
        """Display the "no match" error screen.  Returns ``True`` always."""
        self._stop_vlc_if_active()
        self._router.send_command(
            {"action": "show_html", "src": "tsv6_no_match.html"}
        )
        self._state = "no_match"
        return True

    def show_barcode_not_qr(self) -> bool:
        """
        Display the "barcode is not a QR code" informational screen.

        Returns ``True`` always.
        """
        self._stop_vlc_if_active()
        self._router.send_command(
            {"action": "show_html", "src": "tsv6_barcode_not_qr.html"}
        )
        self._state = "barcode_not_qr"
        return True

    def show_no_item_detected(self) -> bool:
        """
        Display the "item not detected" error screen.  Returns ``True`` always.
        """
        self._stop_vlc_if_active()
        self._router.send_command(
            {"action": "show_html", "src": "tsv6_no_item_detected.html"}
        )
        self._state = "no_item_detected"
        return True

    def show_offline(self) -> bool:
        """Display the offline / no-network screen.  Returns ``True`` always."""
        self._stop_vlc_if_active()
        self._router.send_command(
            {"action": "show_html", "src": "tsv6_offline.html"}
        )
        self._state = "offline"
        return True

    def show_ticker(
        self,
        text: str,
        enabled: bool = True,
        scroll: bool = False,
        speed: int = 3,
    ) -> bool:
        """Update the red footer ticker text. Empty or disabled reverts to default."""
        self._router.send_command(
            {
                "action": "show_ticker",
                "text": text,
                "enabled": enabled,
                "scroll": scroll,
                "speed": speed,
            }
        )
        return True

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """
        Return ``True`` if the Chromium process is running.

        The renderer does not maintain an independent network connection; this
        property reflects whether the display pipeline is operational.
        """
        return self._chromium.is_running()

    # ── Metrics ────────────────────────────────────────────────────────────

    def get_metrics(self) -> dict:
        """
        Return operational metrics for monitoring.

        Keys
        ----
        state : str
            Current display state string.
        chromium_running : bool
        vlc_playing : bool
        main_rect : tuple[int, int, int, int]
        router_url : str
        """
        return {
            "state": self._state,
            "chromium_running": self._chromium.is_running(),
            "vlc_playing": self._vlc.is_playing(),
            "main_rect": self._main_rect,
            "router_url": self._router.url,
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    def _stop_vlc_if_active(self) -> None:
        """Stop VLC and send ``hide_video_zone`` if VLC was playing."""
        if self._vlc.is_playing():
            self._vlc.hide()
            self._router.send_command({"action": "hide_video_zone"})

    def _refresh_main_rect(self) -> None:
        """
        Query the ``#main`` element's bounding box from Chromium via CDP.

        Falls back to the last known rect (or the default) on failure.
        """
        # Prefer the rect that the browser self-reported via POST /video_zone_rect.
        reported = self._router.get_video_zone_rect()
        if reported:
            self._main_rect = reported
            logger.debug("main_rect from browser report: %s", self._main_rect)
            return

        # Fall back to CDP DOM measurement.
        rect = self._chromium.get_zone_rect("#main")
        if rect:
            self._main_rect = rect
            logger.debug("main_rect from CDP: %s", self._main_rect)
        else:
            logger.debug("main_rect: using default %s", self._main_rect)
