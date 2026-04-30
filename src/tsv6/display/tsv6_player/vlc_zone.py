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
calling Tkinter APIs. Public methods that need Tk state enqueue work for the
Tk thread to drain on its next update tick.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

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
    # Disable audio entirely. The kiosk has no speakers and ALSA's "default"
    # PCM is unconfigured, so leaving audio enabled produces hundreds of
    # `snd_pcm_open_noupdate: Unknown PCM default` errors per second that
    # flood journalctl and starve real logs. --aout=none silences the audio
    # output module; --no-audio disables audio decoding/streams as well.
    "--no-audio",
    "--aout=none",
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
        self._window_visible = True
        self._tk_tasks: queue.Queue[tuple[Callable[[], None], str]] = queue.Queue()

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

        # If the Tk window and VLC instance are already alive, swap the
        # playlist in place instead of tearing everything down.  Recreating
        # the vlc.Instance per state transition triggers a libVLC libevent
        # use-after-free that crashes the process with `epoll_ctl: Invalid
        # argument` and exit 133.  Reusing the instance avoids that race
        # entirely.
        if (
            self._running
            and self._vlc_instance is not None
            and self._tk_root is not None
            and self._media_list_player is not None
        ):
            return self._swap_media_list(media_paths, loop, on_playlist_end)

        # First-time start (or recovery after a previous hide): full setup.
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

    def _swap_media_list(
        self,
        media_paths: list[Path],
        loop: bool,
        on_playlist_end: Any,
    ) -> bool:
        """Swap the active playlist on the existing VLC instance.

        Builds a fresh media_list using the live ``_vlc_instance``, schedules
        the actual stop+set+play work on the Tk thread (VLC objects are not
        thread-safe), and returns immediately.  The EndReached callback is
        always attached during ``_tk_main``; this method only updates the
        ``_on_playlist_end`` attribute that the handler reads.
        """
        try:
            import vlc
        except ImportError:
            logger.error("python-vlc not installed; cannot swap media list.")
            return False

        instance = self._vlc_instance
        try:
            new_list = instance.media_list_new()
            for path in media_paths:
                new_list.add_media(instance.media_new(str(path)))
        except Exception as exc:
            logger.error("Failed to build new media list: %s", exc)
            return False

        new_callback = on_playlist_end if not loop else None

        mlp = self._media_list_player

        def _apply_swap() -> None:
            # CRITICAL: disarm the callback BEFORE mlp.stop().  Stopping the
            # current player can synchronously fire a final EndReached event
            # for the media that was still playing — if `_on_playlist_end`
            # is already pointing at the NEW callback, that spurious end
            # would consume the new one (it's one-shot), and the actual end
            # of the new playlist would have no callback to fire.  This is
            # what made the deposit_item playlist hang and never return to
            # idle when scans came in fast enough that processing was still
            # playing when show_deposit_item swapped in.
            self._on_playlist_end = None
            try:
                try:
                    mlp.stop()
                except Exception:
                    pass
                mlp.set_media_list(new_list)
                if loop:
                    mlp.set_playback_mode(vlc.PlaybackMode.loop)
                else:
                    mlp.set_playback_mode(vlc.PlaybackMode.default)
                # Arm the new callback only after the previous media is fully
                # stopped and the new list is loaded.
                self._on_playlist_end = new_callback
                mlp.play()
                logger.info(
                    "VLCZonePlayer: swapped to %d file(s) (loop=%s)",
                    len(media_paths),
                    loop,
                )
            except Exception as exc:
                logger.error("VLCZonePlayer media-list swap failed: %s", exc)

        if not self._enqueue_tk_task(_apply_swap, "media-list swap"):
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
        self._enqueue_tk_task(lambda: self._apply_rect(x, y, w, h), "rect update")

    def pause(self) -> None:
        """Toggle pause on the VLC media player."""
        if self._media_player:
            self._media_player.pause()

    def set_window_visible(self, visible: bool) -> None:
        """Raise or lower the VLC Tk window without destroying libVLC."""
        self._window_visible = visible
        if self._tk_root is None:
            return

        def _apply_visibility() -> None:
            try:
                if visible:
                    self._tk_root.deiconify()
                    self._tk_root.lift()
                    self._tk_root.wm_attributes("-topmost", True)
                else:
                    self._tk_root.wm_attributes("-topmost", False)
                    self._tk_root.lower()
            except Exception as exc:
                logger.warning("VLCZonePlayer visibility change failed: %s", exc)

        self._enqueue_tk_task(_apply_visibility, "visibility change")

    def next(self) -> None:
        """Skip to the next item in the playlist."""
        if self._media_list_player:
            self._media_list_player.next()

    def soft_stop(self) -> None:
        """Stop playback but keep the VLC instance and Tk window alive.

        Use when the display is switching to a non-VLC state (e.g. product
        display HTML) and will need to resume VLC later.  Avoids the
        use-after-free crash (exit 133) that occurs when ``hide()`` destroys
        the libVLC instance and a subsequent ``show()`` recreates it.
        """
        if self._media_list_player:
            try:
                self._media_list_player.stop()
            except Exception:
                pass
        if self._media_player:
            try:
                self._media_player.stop()
            except Exception:
                pass

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
                self._clear_tk_tasks()
                root = self._tk_root
                self._enqueue_tk_task(root.destroy, "Tk destroy")
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
        if not self._window_visible:
            root.withdraw()
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

        # Always attach the EndReached handler so subsequent _swap_media_list
        # calls (which may switch from loop=True to loop=False) get the
        # callback fired without needing to re-attach.  The handler reads
        # the live `_on_playlist_end` attribute and no-ops when it's None.
        # The callback itself must NOT touch Tk/VLC objects directly (doing
        # so from an internal VLC thread causes epoll_ctl crashes); the
        # backend's _return_to_idle wraps show_idle() in a daemon thread.
        def _on_vlc_end(event: Any) -> None:
            cb = self._on_playlist_end
            if cb is not None:
                self._on_playlist_end = None  # one-shot per playlist
                try:
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
            self._drain_tk_tasks()
            try:
                root.update()
            except tk.TclError:
                break
            time.sleep(0.016)  # ~60 Hz poll

        self._drain_tk_tasks()

    def _apply_rect(self, x: int, y: int, w: int, h: int) -> None:
        """Apply a geometry change from within the Tk thread."""
        if self._tk_root:
            self._tk_root.geometry(f"{w}x{h}+{x}+{y}")

    def _enqueue_tk_task(self, task: Callable[[], None], description: str) -> bool:
        if self._tk_root is None:
            return False
        if threading.current_thread() is self._tk_thread:
            try:
                task()
            except Exception as exc:
                logger.warning("VLCZonePlayer %s failed: %s", description, exc)
            return True
        self._tk_tasks.put((task, description))
        return True

    def _drain_tk_tasks(self) -> None:
        while True:
            try:
                task, description = self._tk_tasks.get_nowait()
            except queue.Empty:
                return
            try:
                task()
            except Exception as exc:
                logger.warning("VLCZonePlayer %s failed: %s", description, exc)

    def _clear_tk_tasks(self) -> None:
        while True:
            try:
                self._tk_tasks.get_nowait()
            except queue.Empty:
                return
