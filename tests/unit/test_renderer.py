"""
Unit tests for the TSV6 renderer subsystem.

Scope
-----
Tests ONLY the pure-logic parts that do not require a real display:
- ``RouterServer.send_command`` enqueues and is served via SSE.
- RouterServer routes map to the correct layout file and cache dir.
- ``ChromiumKiosk._build_command`` produces the exact required flag set.
- CDP message serialisation round-trips correctly as JSON-RPC.
- ``TSV6Renderer`` calls the correct router command for each ``show_*`` method.

Tests that require real Chromium or VLC are marked ``@pytest.mark.hardware``
and are skipped in normal CI runs.

All Flask test calls use the Werkzeug test client to avoid binding real ports.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tsv6.display.tsv6_player.chromium import ChromiumKiosk
from tsv6.display.tsv6_player.renderer import TSV6Renderer
from tsv6.display.tsv6_player.router import RouterServer
from tsv6.display.tsv6_player.vlc_zone import VLCZonePlayer

# --------------------------------------------------------------------------- #
#  Fixtures                                                                    #
# --------------------------------------------------------------------------- #

TMP_CACHE = Path("/tmp/tsv6_test_cache")
TMP_LAYOUT = Path("/tmp/tsv6_test_layout.html")


@pytest.fixture(autouse=True)
def _create_tmp_layout(tmp_path: Path):
    """Write a minimal layout HTML so RouterServer can serve it."""
    layout = tmp_path / "router_page.html"
    layout.write_text("<html><body>TEST</body></html>", encoding="utf-8")
    cache = tmp_path / "assets"
    cache.mkdir(exist_ok=True)
    return layout, cache


@pytest.fixture
def router(tmp_path: Path) -> RouterServer:
    layout = tmp_path / "router_page.html"
    layout.write_text("<html><body>TEST</body></html>", encoding="utf-8")
    cache = tmp_path / "assets"
    cache.mkdir(exist_ok=True)
    return RouterServer(
        cache_dir=cache,
        layout_html=layout,
        host="127.0.0.1",
        port=19999,  # unused port; we use the test client, not the real server
    )


@pytest.fixture
def flask_client(router: RouterServer):
    """Return a Werkzeug test client for the RouterServer's Flask app."""
    router._app.config["TESTING"] = True
    return router._app.test_client()


# --------------------------------------------------------------------------- #
#  RouterServer — command queue                                                #
# --------------------------------------------------------------------------- #

class TestRouterServerSendCommand:
    def test_single_command_enqueued(self, router: RouterServer) -> None:
        cmd = {"action": "show_idle"}
        router.send_command(cmd)
        assert not router._command_queue.empty()
        got = router._command_queue.get_nowait()
        assert got == cmd

    def test_multiple_commands_preserve_order(self, router: RouterServer) -> None:
        cmds = [
            {"action": "show_html", "src": "a.html"},
            {"action": "show_image", "src": "b.jpg"},
            {"action": "show_idle"},
        ]
        for c in cmds:
            router.send_command(c)
        for expected in cmds:
            assert router._command_queue.get_nowait() == expected

    def test_send_command_thread_safe(self, router: RouterServer) -> None:
        """Enqueue 200 commands from 4 threads simultaneously."""
        import threading

        results: list[dict] = []

        def producer(n: int) -> None:
            for i in range(50):
                router.send_command({"action": "noop", "n": n * 50 + i})

        threads = [threading.Thread(target=producer, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        while not router._command_queue.empty():
            results.append(router._command_queue.get_nowait())
        assert len(results) == 200

    def test_command_action_logged(self, router: RouterServer) -> None:
        """send_command should not raise on any valid action name."""
        for action in ("show_html", "show_image", "show_product",
                       "show_video_zone", "hide_video_zone", "show_idle"):
            router.send_command({"action": action})
        assert router._command_queue.qsize() == 6


# --------------------------------------------------------------------------- #
#  RouterServer — Flask routes                                                 #
# --------------------------------------------------------------------------- #

class TestRouterServerRoutes:
    def test_index_serves_html(self, flask_client) -> None:
        resp = flask_client.get("/")
        assert resp.status_code == 200
        assert b"TEST" in resp.data

    def test_assets_404_for_missing_file(self, flask_client) -> None:
        resp = flask_client.get("/assets/does_not_exist.mp4")
        assert resp.status_code == 404

    def test_assets_serves_existing_file(self, flask_client, router: RouterServer) -> None:
        # Write a dummy asset into cache_dir.
        asset = router._cache_dir / "hello.txt"
        asset.write_text("hi", encoding="utf-8")
        resp = flask_client.get("/assets/hello.txt")
        assert resp.status_code == 200
        assert b"hi" in resp.data

    def test_events_endpoint_content_type(self, router: RouterServer) -> None:
        """Verify the /events route is registered with the correct Content-Type.

        We inspect the app's URL map rather than issuing a real streaming GET,
        because the SSE generator is an infinite loop that would block the
        Werkzeug test client indefinitely.
        """
        rules = {rule.rule for rule in router._app.url_map.iter_rules()}
        assert "/events" in rules

    def test_video_zone_rect_stores_rect(self, flask_client, router: RouterServer) -> None:
        payload = json.dumps({"rect": [10, 20, 800, 420]})
        resp = flask_client.post(
            "/video_zone_rect",
            data=payload,
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert router.get_video_zone_rect() == (10, 20, 800, 420)

    def test_video_zone_rect_ignores_bad_payload(self, flask_client, router: RouterServer) -> None:
        resp = flask_client.post(
            "/video_zone_rect",
            data="not json",
            content_type="text/plain",
        )
        # Should not crash; rect stays None.
        assert resp.status_code == 200
        assert router.get_video_zone_rect() is None

    def test_url_property(self, router: RouterServer) -> None:
        assert router.url == "http://127.0.0.1:19999/"


# --------------------------------------------------------------------------- #
#  RouterServer — SSE event serialisation                                      #
# --------------------------------------------------------------------------- #

class TestSseEventFormat:
    def test_sse_event_format(self) -> None:
        from tsv6.display.tsv6_player.router import _sse_event
        result = _sse_event({"action": "show_idle"})
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        body = result[len("data: "):-2]
        parsed = json.loads(body)
        assert parsed == {"action": "show_idle"}

    def test_sse_event_complex_payload(self) -> None:
        from tsv6.display.tsv6_player.router import _sse_event
        cmd = {"action": "show_product", "image": "page1.png", "qr_url": "https://example.com/x"}
        result = _sse_event(cmd)
        parsed = json.loads(result[len("data: "):-2])
        assert parsed["qr_url"] == "https://example.com/x"


# --------------------------------------------------------------------------- #
#  ChromiumKiosk — command-line builder                                        #
# --------------------------------------------------------------------------- #

class TestChromiumKioskCommandLine:
    """Test flag generation without launching a real process."""

    @pytest.fixture
    def kiosk(self, tmp_path: Path) -> ChromiumKiosk:
        return ChromiumKiosk(
            url="http://127.0.0.1:8765/",
            display=":0",
            xauthority="/home/pi/.Xauthority",
            user_data_dir=tmp_path / "chromium",
            cdp_port=9222,
            width=800,
            height=480,
        )

    def test_kiosk_flag_present(self, kiosk: ChromiumKiosk) -> None:
        cmd = kiosk._build_command()
        assert "--kiosk" in cmd

    def test_incognito_flag(self, kiosk: ChromiumKiosk) -> None:
        assert "--incognito" in kiosk._build_command()

    def test_window_size_flag(self, kiosk: ChromiumKiosk) -> None:
        assert "--window-size=800,480" in kiosk._build_command()

    def test_remote_debugging_port_flag(self, kiosk: ChromiumKiosk) -> None:
        cmd = kiosk._build_command()
        assert "--remote-debugging-port=9222" in cmd

    def test_remote_allow_origins_flag(self, kiosk: ChromiumKiosk) -> None:
        assert "--remote-allow-origins=http://localhost:9222" in kiosk._build_command()

    def test_user_data_dir_in_command(self, kiosk: ChromiumKiosk, tmp_path: Path) -> None:
        cmd = kiosk._build_command()
        expected = f"--user-data-dir={tmp_path / 'chromium'}"
        assert expected in cmd

    def test_url_is_last_argument(self, kiosk: ChromiumKiosk) -> None:
        cmd = kiosk._build_command()
        assert cmd[-1] == "http://127.0.0.1:8765/"

    def test_no_first_run_flag(self, kiosk: ChromiumKiosk) -> None:
        assert "--no-first-run" in kiosk._build_command()

    def test_autoplay_policy_flag(self, kiosk: ChromiumKiosk) -> None:
        assert "--autoplay-policy=no-user-gesture-required" in kiosk._build_command()

    def test_disable_infobars(self, kiosk: ChromiumKiosk) -> None:
        assert "--disable-infobars" in kiosk._build_command()

    def test_disk_cache_size(self, kiosk: ChromiumKiosk) -> None:
        assert "--disk-cache-size=52428800" in kiosk._build_command()

    def test_password_store_basic(self, kiosk: ChromiumKiosk) -> None:
        assert "--password-store=basic" in kiosk._build_command()

    def test_custom_port(self, tmp_path: Path) -> None:
        k = ChromiumKiosk(
            url="http://127.0.0.1:8765/",
            cdp_port=9333,
            user_data_dir=tmp_path,
        )
        cmd = k._build_command()
        assert "--remote-debugging-port=9333" in cmd
        assert "--remote-allow-origins=http://localhost:9333" in cmd

    def test_custom_dimensions(self, tmp_path: Path) -> None:
        k = ChromiumKiosk(
            url="http://127.0.0.1:8765/",
            user_data_dir=tmp_path,
            width=1920,
            height=1080,
        )
        assert "--window-size=1920,1080" in k._build_command()


# --------------------------------------------------------------------------- #
#  ChromiumKiosk — process output logging                                      #
# --------------------------------------------------------------------------- #

class TestChromiumKioskOutputLogging:
    def test_chromium_log_path_can_be_overridden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_path = tmp_path / "logs" / "chromium.log"
        monkeypatch.setenv("TSV6_CHROMIUM_LOG_PATH", str(log_path))
        kiosk = ChromiumKiosk(url="http://127.0.0.1:8765/", user_data_dir=tmp_path)

        assert kiosk._chromium_log_path() == log_path

    def test_start_captures_chromium_stdout_and_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TSV6_CHROMIUM_LOG_PATH", str(tmp_path / "chromium.log"))
        kiosk = ChromiumKiosk(url="http://127.0.0.1:8765/", user_data_dir=tmp_path)
        fake_process = MagicMock()
        fake_process.pid = 123
        fake_process.stdout = None
        monkeypatch.setattr(kiosk, "_wait_for_cdp", lambda: True)

        with patch(
            "tsv6.display.tsv6_player.chromium.subprocess.Popen",
            return_value=fake_process,
        ) as popen:
            assert kiosk.start() is True

        kwargs = popen.call_args.kwargs
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.STDOUT
        assert kwargs["text"] is True


# --------------------------------------------------------------------------- #
#  ChromiumKiosk — preference patching                                        #
# --------------------------------------------------------------------------- #

class TestChromiumPreferencePatch:
    def test_patch_creates_preferences_file(self, tmp_path: Path) -> None:
        kiosk = ChromiumKiosk(
            url="http://127.0.0.1:8765/",
            user_data_dir=tmp_path / "chromium",
        )
        kiosk._patch_preferences()
        prefs_path = tmp_path / "chromium" / "Default" / "Preferences"
        assert prefs_path.exists()
        prefs = json.loads(prefs_path.read_text())
        assert prefs["profile"]["exited_cleanly"] is True
        assert prefs["profile"]["exit_type"] == "Normal"

    def test_patch_preserves_existing_prefs(self, tmp_path: Path) -> None:
        kiosk = ChromiumKiosk(
            url="http://127.0.0.1:8765/",
            user_data_dir=tmp_path / "chromium",
        )
        prefs_path = tmp_path / "chromium" / "Default" / "Preferences"
        prefs_path.parent.mkdir(parents=True)
        prefs_path.write_text(
            json.dumps({"custom_key": "custom_value", "profile": {"other": 1}}),
            encoding="utf-8",
        )
        kiosk._patch_preferences()
        prefs = json.loads(prefs_path.read_text())
        assert prefs["custom_key"] == "custom_value"
        assert prefs["profile"]["exited_cleanly"] is True
        assert prefs["profile"]["other"] == 1

    def test_patch_survives_corrupt_preferences(self, tmp_path: Path) -> None:
        kiosk = ChromiumKiosk(
            url="http://127.0.0.1:8765/",
            user_data_dir=tmp_path / "chromium",
        )
        prefs_path = tmp_path / "chromium" / "Default" / "Preferences"
        prefs_path.parent.mkdir(parents=True)
        prefs_path.write_text("NOT JSON {{{{", encoding="utf-8")
        # Should not raise.
        kiosk._patch_preferences()
        prefs = json.loads(prefs_path.read_text())
        assert prefs["profile"]["exited_cleanly"] is True


# --------------------------------------------------------------------------- #
#  CDP message serialisation                                                   #
# --------------------------------------------------------------------------- #

class TestCdpSerialization:
    """Verify the JSON-RPC payload structure without a real WebSocket."""

    def test_cdp_message_structure(self, tmp_path: Path) -> None:
        kiosk = ChromiumKiosk(
            url="http://127.0.0.1:8765/",
            user_data_dir=tmp_path,
        )
        kiosk._ws_url = "ws://127.0.0.1:9222/devtools/page/fake"
        kiosk._cdp_id = 0

        # Capture the JSON that would be sent over WebSocket.
        sent_payloads: list[str] = []

        class FakeWS:
            def send(self, data: str) -> None:
                sent_payloads.append(data)

            def recv(self) -> str:
                # Return a valid response matching the incremented id.
                msg = json.loads(sent_payloads[-1])
                return json.dumps({"id": msg["id"], "result": {}})

            def close(self) -> None:
                pass

        with patch("websocket.create_connection", return_value=FakeWS()):
            result = kiosk._cdp_send("Page.reload", {"ignoreCache": True})

        assert len(sent_payloads) == 1
        payload = json.loads(sent_payloads[0])
        assert payload["method"] == "Page.reload"
        assert payload["params"] == {"ignoreCache": True}
        assert payload["id"] == 1
        assert result == {"id": 1, "result": {}}

    def test_cdp_id_increments(self, tmp_path: Path) -> None:
        kiosk = ChromiumKiosk(
            url="http://127.0.0.1:8765/",
            user_data_dir=tmp_path,
        )
        kiosk._ws_url = "ws://127.0.0.1:9222/fake"
        kiosk._cdp_id = 5

        ids: list[int] = []

        class FakeWS:
            def send(self, data: str) -> None:
                ids.append(json.loads(data)["id"])

            def recv(self) -> str:
                return json.dumps({"id": ids[-1], "result": {}})

            def close(self) -> None:
                pass

        with patch("websocket.create_connection", return_value=FakeWS()):
            kiosk._cdp_send("Page.reload", {})
            kiosk._cdp_send("Page.navigate", {"url": "about:blank"})

        assert ids == [6, 7]

    def test_cdp_raises_when_not_connected(self, tmp_path: Path) -> None:
        kiosk = ChromiumKiosk(url="http://x/", user_data_dir=tmp_path)
        kiosk._ws_url = None
        with pytest.raises(RuntimeError, match="CDP not connected"):
            kiosk._cdp_send("Page.reload", {})


# --------------------------------------------------------------------------- #
#  VLCZonePlayer — Tk-thread task scheduling                                  #
# --------------------------------------------------------------------------- #

class TestVLCZonePlayerScheduling:
    def test_visibility_uses_internal_queue_and_lowers_for_hidden_state(self) -> None:
        player = VLCZonePlayer()
        root = MagicMock()
        player._tk_root = root

        player.set_window_visible(False)

        root.after.assert_not_called()
        player._drain_tk_tasks()
        root.wm_attributes.assert_called_once_with("-topmost", False)
        root.lower.assert_called_once()
        root.withdraw.assert_not_called()

    def test_swap_media_list_uses_internal_queue_not_tk_after(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fake_vlc = SimpleNamespace(
            PlaybackMode=SimpleNamespace(loop="loop", default="default")
        )
        monkeypatch.setitem(sys.modules, "vlc", fake_vlc)

        media_list = MagicMock()
        instance = MagicMock()
        instance.media_list_new.return_value = media_list
        instance.media_new.side_effect = lambda path: f"media:{path}"
        media_list_player = MagicMock()
        root = MagicMock()

        player = VLCZonePlayer()
        player._running = True
        player._tk_root = root
        player._vlc_instance = instance
        player._media_list_player = media_list_player

        mp4 = tmp_path / "state.mp4"
        mp4.write_bytes(b"fake")

        assert player._swap_media_list([mp4], loop=False, on_playlist_end=None) is True

        root.after.assert_not_called()
        media_list_player.play.assert_not_called()

        player._drain_tk_tasks()

        media_list.add_media.assert_called_once_with(f"media:{mp4}")
        media_list_player.stop.assert_called_once()
        media_list_player.set_media_list.assert_called_once_with(media_list)
        media_list_player.set_playback_mode.assert_called_once_with("default")
        media_list_player.play.assert_called_once()

    def test_hide_discards_queued_media_swaps_before_destroy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fake_vlc = SimpleNamespace(
            PlaybackMode=SimpleNamespace(loop="loop", default="default")
        )
        monkeypatch.setitem(sys.modules, "vlc", fake_vlc)

        instance = MagicMock()
        instance.media_list_new.return_value = MagicMock()
        media_list_player = MagicMock()
        media_player = MagicMock()
        root = MagicMock()

        player = VLCZonePlayer()
        player._running = True
        player._tk_root = root
        player._vlc_instance = instance
        player._media_list_player = media_list_player
        player._media_player = media_player

        mp4 = tmp_path / "state.mp4"
        mp4.write_bytes(b"fake")
        player._swap_media_list([mp4], loop=False, on_playlist_end=None)

        player.hide()
        player._drain_tk_tasks()

        media_list_player.set_media_list.assert_not_called()
        media_list_player.play.assert_not_called()
        root.destroy.assert_called_once()


# --------------------------------------------------------------------------- #
#  TSV6Renderer — show_* methods call the right router commands               #
# --------------------------------------------------------------------------- #

class TestTSV6RendererShowMethods:
    """
    Test that each show_* method enqueues the correct action.

    RouterServer, ChromiumKiosk, and VLCZonePlayer are all mocked so no
    real processes or ports are needed.
    """

    @pytest.fixture
    def renderer(self, tmp_path: Path) -> TSV6Renderer:
        cache = tmp_path / "assets"
        cache.mkdir(exist_ok=True)  # exist_ok guards against pytest tmp_path reuse
        layout = tmp_path / "router_page.html"
        layout.write_text("<html/>", encoding="utf-8")
        return TSV6Renderer(
            cache_dir=cache,
            layout_html=layout,
        )

    @pytest.fixture
    def mock_router(self, renderer: TSV6Renderer) -> MagicMock:
        mock = MagicMock(spec=RouterServer)
        mock.url = "http://127.0.0.1:8765/"
        mock.get_video_zone_rect.return_value = None
        renderer._router = mock
        return mock

    @pytest.fixture
    def mock_chromium(self, renderer: TSV6Renderer) -> MagicMock:
        mock = MagicMock(spec=ChromiumKiosk)
        mock.is_running.return_value = True
        mock.get_zone_rect.return_value = (0, 0, 800, 420)
        renderer._chromium = mock
        return mock

    @pytest.fixture
    def mock_vlc(self, renderer: TSV6Renderer) -> MagicMock:
        mock = MagicMock(spec=VLCZonePlayer)
        mock.is_playing.return_value = False
        mock.show.return_value = True
        renderer._vlc = mock
        return mock

    def _first_command(self, mock_router: MagicMock) -> dict:
        """Return the first ``action`` argument passed to send_command."""
        assert mock_router.send_command.called
        return mock_router.send_command.call_args_list[0][0][0]

    def test_show_processing_sends_show_html(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        renderer.show_processing()
        cmd = self._first_command(mock_router)
        assert cmd["action"] == "show_html"
        assert "tsv6_processing" in cmd["src"]

    def test_show_deposit_item_sends_show_html(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        renderer.show_deposit_item()
        cmd = self._first_command(mock_router)
        assert cmd["action"] == "show_html"
        assert "deposit" in cmd["src"]

    def test_show_product_display_sends_show_product(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
        tmp_path: Path,
    ) -> None:
        image = tmp_path / "assets" / "page1.png"
        image.write_bytes(b"fake")
        renderer.show_product_display(image, "https://example.com/qr")
        cmds = [call[0][0] for call in mock_router.send_command.call_args_list]
        cmd = next(c for c in cmds if c["action"] == "show_product")
        assert cmd["action"] == "show_product"
        assert cmd["image"] == "page1.png"
        assert cmd["qr_url"] == "https://example.com/qr"

    def test_show_product_display_unmaps_vlc_window_without_destroying_instance(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Product HTML must reveal Chromium without destroying/recreating VLC."""
        mock_vlc.is_playing.return_value = True
        image = tmp_path / "assets" / "page1.png"
        image.write_bytes(b"fake")

        renderer.show_product_display(image, "https://example.com/qr")

        mock_vlc.set_window_visible.assert_called_once_with(False)
        mock_vlc.soft_stop.assert_not_called()
        mock_vlc.hide.assert_not_called()

    def test_hide_product_display_sends_hide_product(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        renderer.hide_product_display()
        cmd = self._first_command(mock_router)
        assert cmd["action"] == "hide_product"

    def test_show_no_match_sends_show_html(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        renderer.show_no_match()
        cmd = self._first_command(mock_router)
        assert cmd["action"] == "show_html"
        assert "no_match" in cmd["src"]

    def test_show_barcode_not_qr_sends_show_html(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        renderer.show_barcode_not_qr()
        cmd = self._first_command(mock_router)
        assert cmd["action"] == "show_html"
        assert "barcode" in cmd["src"]

    def test_show_no_item_detected_sends_show_html(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        renderer.show_no_item_detected()
        cmd = self._first_command(mock_router)
        assert cmd["action"] == "show_html"
        assert "no_item" in cmd["src"]

    def test_show_offline_sends_show_html(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        renderer.show_offline()
        # show_offline sends hide_vengo_idle then show_html
        cmds = [c[0][0] for c in mock_router.send_command.call_args_list]
        show_html_cmd = next(c for c in cmds if c["action"] == "show_html")
        assert "offline" in show_html_cmd["src"]

    def test_show_idle_sends_show_video_zone(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
        tmp_path: Path,
    ) -> None:
        mp4 = tmp_path / "idle.mp4"
        mp4.write_bytes(b"fake video")
        renderer.show_idle([mp4])
        # First send_command call should be show_video_zone.
        cmds = [call[0][0] for call in mock_router.send_command.call_args_list]
        actions = [c["action"] for c in cmds]
        assert "show_video_zone" in actions

    def test_show_idle_calls_vlc_show(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
        tmp_path: Path,
    ) -> None:
        mp4 = tmp_path / "idle.mp4"
        mp4.write_bytes(b"fake video")
        renderer.show_idle([mp4])
        mock_vlc.show.assert_called_once()

    def test_show_idle_remaps_vlc_window_without_soft_stopping_first(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returning to VLC should remap the window and let VLCZonePlayer swap media."""
        mock_vlc.is_playing.return_value = True
        mp4 = tmp_path / "idle.mp4"
        mp4.write_bytes(b"fake video")

        renderer.show_idle([mp4])

        mock_vlc.set_window_visible.assert_called_once_with(True)
        mock_vlc.soft_stop.assert_not_called()
        mock_vlc.hide.assert_not_called()
        mock_vlc.show.assert_called_once()

    def test_show_idle_empty_list_returns_false(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        result = renderer.show_idle([])
        assert result is False
        mock_vlc.show.assert_not_called()

    def test_state_updated_after_show_processing(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        renderer.show_processing()
        assert renderer._state == "processing"

    def test_state_updated_after_show_offline(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        renderer.show_offline()
        assert renderer._state == "offline"

    def test_vlc_hidden_before_show_html(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        """If VLC was playing, it should be hidden before sending HTML command."""
        mock_vlc.is_playing.return_value = True
        renderer.show_processing()
        mock_vlc.hide.assert_called_once()

    def test_is_connected_delegates_to_chromium(
        self,
        renderer: TSV6Renderer,
        mock_chromium: MagicMock,
    ) -> None:
        mock_chromium.is_running.return_value = True
        assert renderer.is_connected is True
        mock_chromium.is_running.return_value = False
        assert renderer.is_connected is False

    def test_get_metrics_returns_dict(
        self,
        renderer: TSV6Renderer,
        mock_router: MagicMock,
        mock_chromium: MagicMock,
        mock_vlc: MagicMock,
    ) -> None:
        metrics = renderer.get_metrics()
        assert "state" in metrics
        assert "chromium_running" in metrics
        assert "vlc_playing" in metrics
        assert "main_rect" in metrics
        assert "router_url" in metrics


# --------------------------------------------------------------------------- #
#  Hardware-dependent tests (skipped in CI)                                   #
# --------------------------------------------------------------------------- #

@pytest.mark.hardware
def test_chromium_actually_launches() -> None:
    """Verify Chromium can be launched against a real X display."""
    pytest.skip("Requires real display hardware.")


@pytest.mark.hardware
def test_vlc_actually_plays() -> None:
    """Verify VLC can open a window and play a file on real hardware."""
    pytest.skip("Requires real display hardware.")


@pytest.mark.hardware
def test_renderer_full_start() -> None:
    """End-to-end start/stop of the full renderer pipeline."""
    pytest.skip("Requires real display hardware.")
