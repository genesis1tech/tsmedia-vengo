"""
VLC-based video player that renders inside a Tk window positioned over a
Chromium zone.

The Tk window is created as a borderless, ``overrideredirect`` window so it
has no title bar, decorations, or window-manager frame.  VLC embeds its video
output into the Tk window's X11 window ID via ``set_xwindow``.

A ``MediaListPlayer`` is used so that a list of media paths can be looped
seamlessly without manual end-of-media tracking.

VLC args used on Raspberry Pi 5 (see ``_DEFAULT_VLC_ARGS``):
- ``--avcodec-hw=any``  — use hardware decoding (V4L2/MMAL on Pi 5).
- ``--vout=gles2,xcb_x11``  — GLES2 preferred, xcb_x11 fallback.
- ``--file-caching=1000`` / ``--network-caching=1500``  — generous I/O cache.
- ``--quiet --intf=dummy``  — suppress OSD and keyboard/mouse interfaces.

Thread-safety
-------------
All public methods are designed to be called from one orchestration thread.
The ``_tk_thread`` owns the Tkinter event loop and must be the only thread
calling Tkinter APIs.  Public methods that need Tk state schedule work via
``_tk_root.after(0, ...)`` where necessary, but for most operations the
caller does not need a synchronous result.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# VLC instance args optimised for Raspberry Pi 5.
_DEFAULT_VLC_ARGS: list[str] = [
    "--no-osd",
    "--no-video-title-show",
    "--no-snapshot-preview",
    "--no-mouse-events",
    "--no-keyboard-events",
    "--quiet",
    "--intf=dummy",
    "--avcodec-hw=any",
    "--vout=gles2,xcb_x11",
    "--file-caching=1000",
    "--network-caching=1500",
]


class VLCZonePlayer:
    """
    Plays MP4/video files in a Tk window positioned over a Chromium zone.

    Parameters
    ----------
    vlc_args:
        Override the default VLC instance arguments.  Pass ``None`` to use
        ``_DEFAULT_VLC_ARGS``.

    Example
    -------
    ::

        player = VLCZonePlayer()
        rect = (0, 0, 800, 420)          # x, y, w, h in screen pixels
        player.show(rect, [Path("/var/tsv6/assets/idle.mp4")], loop=True)
        ...
        player.stop()
    """

    def __init__(self, vlc_args: list[str] | None = None) -> None:
        self._vlc_args: list[str] = vlc_args if vlc_args is not None else _DEFAULT_VLC_ARGS

        # Tk / VLC objects — only valid between show() and hide()/stop().
        self._tk_root: Any = None      # tkinter.Tk
        self._tk_thread: threading.Thread | None = None
        self._vlc_instance: Any = None
        self._media_list_player: Any = None
        self._media_player: Any = None

        self._running = False
        self._ready_event = threading.Event()
        self._on_playlist_end: Any = None  # callable invoked when non-loop playlist finishes

    # ── Public API ─────────────────────────────────────────────────────────

    def show(
        self,
        rect: tuple[int, int, int, int],
        media_paths: list[Path],
        loop: bool = True,
        on_playlist_end: Any = None,
    ) -> bool:
        """
        Create a borderless Tk window at *rect* and start playing *media_paths*.

        Parameters
        ----------
        rect:
            ``(x, y, width, height)`` in screen pixels.
        media_paths:
            Ordered list of media files to play.  Must be non-empty.
        loop:
            If ``True`` the playlist repeats indefinitely.
        on_playlist_end:
            Optional callback invoked when a non-loop playlist finishes
            playing all items.  Ignored when ``loop=True``.

        Returns
        -------
        bool
            ``True`` if playback started successfully.
        """
        if not media_paths:
            logger.error("VLCZonePlayer.show: media_paths is empty.")
            return False

        if self._running:
            self.hide()

        self._on_playlist_end = on_playlist_end if not loop else None
        self._ready_event.clear()
        self._running = True
        self._tk_thread = threading.Thread(
            target=self._tk_main,
            args=(rect, media_paths, loop),
            name="vlc-zone-tk",
            daemon=True,
        )
        self._tk_thread.start()

        # Wait up to 5 seconds for the Tk window and VLC to initialise.
        if not self._ready_event.wait(timeout=5.0):
            logger.warning("VLCZonePlayer: Tk/VLC did not initialise within 5 s.")
            return False
        return True

    def update_rect(self, rect: tuple[int, int, int, int]) -> None:
        """
        Move and resize the Tk window to *rect*.

        The change takes effect on the next Tkinter event loop iteration.
        """
        if self._tk_root is None:
            logger.warning("update_rect called before show().")
            return
        x, y, w, h = rect
        self._tk_root.after(0, lambda: self._apply_rect(x, y, w, h))

    def pause(self) -> None:
        """Toggle pause on the VLC media player."""
        if self._media_player:
            self._media_player.pause()

    def next(self) -> None:
        """Skip to the next item in the playlist."""
        if self._media_list_player:
            self._media_list_player.next()

    def stop(self) -> None:
        """Stop playback and destroy the Tk window."""
        self.hide()

    def hide(self) -> None:
        """Stop VLC and destroy the Tk window."""
        self._running = False
        if self._media_list_player:
            try:
                self._media_list_player.stop()
            except Exception:
                pass
            self._media_list_player = None
        if self._media_player:
            try:
                self._media_player.stop()
            except Exception:
                pass
            self._media_player = None
        if self._vlc_instance:
            try:
                self._vlc_instance.release()
            except Exception:
                pass
            self._vlc_instance = None
        if self._tk_root:
            try:
                self._tk_root.after(0, self._tk_root.destroy)
            except Exception:
                pass
            self._tk_root = None
        if self._tk_thread:
            self._tk_thread.join(timeout=3.0)
            self._tk_thread = None
        logger.debug("VLCZonePlayer hidden.")

    def is_playing(self) -> bool:
        """Return ``True`` if VLC is currently playing."""
        if self._media_player is None:
            return False
        try:
            return bool(self._media_player.is_playing())
        except Exception:
            return False

    # ── Internal Tk / VLC setup ────────────────────────────────────────────

    def _tk_main(
        self,
        rect: tuple[int, int, int, int],
        media_paths: list[Path],
        loop: bool,
    ) -> None:
        """
        Entry point for the Tk thread.  Creates the window, embeds VLC, and
        runs the Tkinter event loop.
        """
        try:
            import tkinter as tk
        except ImportError:
            logger.error("tkinter is not available; cannot create VLC window.")
            self._ready_event.set()
            return

        try:
            import vlc
        except ImportError:
            logger.error("python-vlc is not installed.")
            self._ready_event.set()
            return

        x, y, w, h = rect

        # Create borderless window.
        root = tk.Tk()
        root.overrideredirect(True)
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.configure(background="black")
        root.wm_attributes("-topmost", True)
        self._tk_root = root

        # VLC instance.
        instance = vlc.Instance(self._vlc_args)
        self._vlc_instance = instance

        # Build media list.
        media_list = instance.media_list_new()
        for path in media_paths:
            media = instance.media_new(str(path))
            media_list.add_media(media)

        # Create MediaListPlayer and bind to the Tk window.
        mlp = instance.media_list_player_new()
        self._media_list_player = mlp
        mlp.set_media_list(media_list)

        mp = mlp.get_media_player()
        self._media_player = mp

        # Embed VLC into the Tk window using the X11 window ID.
        wid = root.winfo_id()
        mp.set_xwindow(wid)

        # Stretch the video to fill the zone (override source DAR).
        mp.video_set_aspect_ratio(f"{w}:{h}".encode("ascii"))

        if loop:
            mlp.set_playback_mode(vlc.PlaybackMode.loop)
        elif self._on_playlist_end is not None:
            # When not looping, fire the callback once the last item finishes.
            def _on_vlc_end(event: Any) -> None:
                try:
                    cb = self._on_playlist_end
                    if cb is not None:
                        self._on_playlist_end = None  # one-shot
                        cb()
                except Exception as exc:
                    logger.warning("on_playlist_end callback error: %s", exc)

            mp.event_manager().event_attach(
                vlc.EventType.MediaPlayerEndReached, _on_vlc_end
            )

        mlp.play()
        logger.info(
            "VLCZonePlayer: playing %d file(s) at %s (loop=%s)",
            len(media_paths),
            rect,
            loop,
        )

        # Signal that initialisation is complete.
        root.after(500, self._ready_event.set)

        # Run the Tk event loop until self._running becomes False.
        while self._running:
            try:
                root.update()
            except tk.TclError:
                break
            time.sleep(0.016)  # ~60 Hz poll

    def _apply_rect(self, x: int, y: int, w: int, h: int) -> None:
        """Apply a geometry change from within the Tk thread."""
        if self._tk_root:
            self._tk_root.geometry(f"{w}x{h}+{x}+{y}")
