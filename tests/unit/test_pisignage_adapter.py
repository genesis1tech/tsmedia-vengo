#!/usr/bin/env python3
"""
Unit tests for PiSignage adapter, health monitor, and playlist manager.

All HTTP calls are mocked — these tests run without a PiSignage server.
"""

import pytest
from unittest.mock import patch, MagicMock, mock_open
import requests

from tsv6.display.pisignage_adapter import PiSignageAdapter, PiSignageConfig
from tsv6.display.pisignage_health import PiSignageHealthMonitor


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return PiSignageConfig(
        server_url="http://test-server:3000",
        username="testuser",
        password="testpass",
    )


@pytest.fixture
def adapter(config):
    return PiSignageAdapter(config=config)


@pytest.fixture
def connected_adapter(adapter):
    """Adapter with a discovered player."""
    adapter._player_id = "player123"
    adapter._player_cpu_serial = "ABCD1234"
    adapter._connected = True
    return adapter


def _mock_response(json_data=None, status_code=200):
    """Create a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status.return_value = None
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


# ── PiSignageAdapter Tests ───────────────────────────────────────────────────

class TestPiSignageAdapterConnect:
    """Tests for adapter.connect() — player discovery."""

    @patch("requests.get")
    def test_connect_discovers_first_player(self, mock_get, adapter):
        mock_get.return_value = _mock_response({
            "success": True,
            "data": {"objects": [
                {"_id": "player_abc", "cpuSerialNumber": "1234ABCD"},
            ]},
        })
        assert adapter.connect() is True
        assert adapter._player_id == "player_abc"
        assert adapter._player_cpu_serial == "1234ABCD"
        assert adapter.is_connected is True

    @patch("requests.get")
    def test_connect_no_players_returns_false(self, mock_get, adapter):
        mock_get.return_value = _mock_response({
            "success": True,
            "data": {"objects": []},
        })
        assert adapter.connect() is False
        assert adapter.is_connected is False

    @patch("requests.get")
    def test_connect_handles_connection_error(self, mock_get, adapter):
        mock_get.side_effect = requests.ConnectionError("unreachable")
        assert adapter.connect() is False
        assert adapter.is_connected is False

    @patch("requests.get")
    def test_connect_handles_list_format(self, mock_get, adapter):
        """Some PiSignage versions return a flat list instead of paginated."""
        mock_get.return_value = _mock_response({
            "success": True,
            "data": [{"_id": "p1", "cpuSerialNumber": "X"}],
        })
        assert adapter.connect() is True
        assert adapter._player_id == "p1"

    @patch("requests.get")
    def test_connect_fires_callback(self, mock_get, adapter):
        callback = MagicMock()
        adapter._on_connection_change = callback
        mock_get.return_value = _mock_response({
            "success": True,
            "data": {"objects": [{"_id": "p1", "cpuSerialNumber": "X"}]},
        })
        adapter.connect()
        callback.assert_called_once_with(True)


class TestPiSignageAdapterDisconnect:

    def test_disconnect_clears_state(self, connected_adapter):
        connected_adapter.disconnect()
        assert connected_adapter.is_connected is False
        assert connected_adapter._player_id is None


class TestPiSignageAdapterPlaylistSwitch:

    @patch("requests.post")
    def test_switch_playlist_success(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        assert connected_adapter.switch_playlist("tsv6_processing") is True
        assert connected_adapter._current_playlist == "tsv6_processing"
        assert "setplaylist/player123/tsv6_processing" in mock_post.call_args[0][0]

    @patch("requests.post")
    def test_switch_playlist_uses_basic_auth(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        connected_adapter.switch_playlist("tsv6_idle_loop")
        _, kwargs = mock_post.call_args
        assert kwargs["auth"].username == "testuser"
        assert kwargs["auth"].password == "testpass"

    def test_switch_playlist_without_player_returns_false(self, adapter):
        assert adapter.switch_playlist("tsv6_idle_loop") is False

    @patch("requests.post")
    def test_switch_retries_on_timeout(self, mock_post, connected_adapter):
        """Should retry up to max_retries on ConnectionError/Timeout."""
        mock_post.side_effect = [
            requests.Timeout("timeout"),
            _mock_response({"success": True}),
        ]
        # Override retry delay to speed up test
        connected_adapter._config = PiSignageConfig(
            server_url="http://test:3000",
            username="u", password="p",
            retry_base_delay=0.01,
            max_retries=3,
        )
        assert connected_adapter.switch_playlist("tsv6_idle") is True
        assert mock_post.call_count == 2

    @patch("requests.post")
    def test_switch_fails_after_max_retries(self, mock_post, connected_adapter):
        mock_post.side_effect = requests.ConnectionError("down")
        connected_adapter._config = PiSignageConfig(
            server_url="http://test:3000",
            username="u", password="p",
            retry_base_delay=0.01,
            max_retries=2,
        )
        assert connected_adapter.switch_playlist("tsv6_idle") is False
        assert connected_adapter._failed_switches == 1

    @patch("requests.post")
    def test_switch_no_retry_on_http_error(self, mock_post, connected_adapter):
        """4xx errors should not be retried."""
        mock_post.return_value = _mock_response(status_code=404)
        assert connected_adapter.switch_playlist("nonexistent") is False
        assert mock_post.call_count == 1


class TestPiSignageAdapterConvenienceMethods:

    @patch("requests.post")
    def test_set_default_playlist(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        assert connected_adapter.set_default_playlist() is True
        assert "tsv6_idle_loop" in mock_post.call_args[0][0]

    @patch("requests.post")
    def test_show_processing(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        assert connected_adapter.show_processing() is True
        assert "tsv6_processing" in mock_post.call_args[0][0]

    @patch("requests.post")
    def test_show_no_match(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        assert connected_adapter.show_no_match() is True
        assert "tsv6_no_match" in mock_post.call_args[0][0]


class TestPiSignageAdapterResolvePlaylist:
    """Validation/fallback for AWS-supplied playlist override names."""

    def test_none_returns_default(self, adapter):
        assert adapter._resolve_playlist(None, "tsv6_processing") == "tsv6_processing"

    def test_empty_string_returns_default(self, adapter):
        assert adapter._resolve_playlist("", "tsv6_processing") == "tsv6_processing"

    def test_non_string_returns_default(self, adapter):
        assert adapter._resolve_playlist(123, "tsv6_processing") == "tsv6_processing"
        assert adapter._resolve_playlist(["x"], "tsv6_processing") == "tsv6_processing"

    def test_valid_name_returns_override(self, adapter):
        assert adapter._resolve_playlist("pepsi_spring26", "tsv6_default") == "pepsi_spring26"

    def test_name_with_dot_dash_underscore_allowed(self, adapter):
        assert adapter._resolve_playlist("a.b-c_1", "tsv6_default") == "a.b-c_1"

    def test_name_with_slash_falls_back(self, adapter, caplog):
        with caplog.at_level("WARNING"):
            assert adapter._resolve_playlist("../etc/passwd", "tsv6_default") == "tsv6_default"
        assert "invalid playlist name" in caplog.text

    def test_name_with_space_falls_back(self, adapter):
        assert adapter._resolve_playlist("bad name", "tsv6_default") == "tsv6_default"

    def test_name_too_long_falls_back(self, adapter):
        assert adapter._resolve_playlist("x" * 65, "tsv6_default") == "tsv6_default"

    def test_max_length_64_allowed(self, adapter):
        name = "x" * 64
        assert adapter._resolve_playlist(name, "tsv6_default") == name


class TestPiSignageAdapterAssets:

    @patch("requests.post")
    def test_upload_asset_success(self, mock_post, connected_adapter, tmp_path):
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"\xff\xd8test image data")
        mock_post.return_value = _mock_response({"success": True})
        assert connected_adapter.upload_asset(str(test_file)) is True

    def test_upload_asset_missing_file(self, connected_adapter):
        assert connected_adapter.upload_asset("/nonexistent/file.jpg") is False

    @patch("requests.post")
    def test_upload_asset_handles_server_error(self, mock_post, connected_adapter, tmp_path):
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"data")
        mock_post.side_effect = requests.ConnectionError("down")
        assert connected_adapter.upload_asset(str(test_file)) is False


class TestPiSignageAdapterPlaylistManagement:

    @patch("requests.post")
    def test_create_playlist(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        assert connected_adapter.create_playlist("test_playlist") is True

    @patch("requests.get")
    def test_list_playlists(self, mock_get, connected_adapter):
        mock_get.return_value = _mock_response({
            "data": [{"name": "playlist1"}, {"name": "playlist2"}]
        })
        result = connected_adapter.list_playlists()
        assert len(result) == 2


class TestPiSignageAdapterHealthCheck:

    @patch("requests.get")
    def test_health_check_healthy(self, mock_get, adapter):
        mock_get.return_value = _mock_response({"success": True})
        assert adapter.health_check() is True

    @patch("requests.get")
    def test_health_check_unhealthy(self, mock_get, adapter):
        mock_get.side_effect = requests.ConnectionError("down")
        assert adapter.health_check() is False


class TestPiSignageAdapterMetrics:

    @patch("tsv6.display.pisignage_adapter.time.monotonic", side_effect=[0.0, 0.075])
    @patch("requests.post")
    def test_metrics_after_switch(self, mock_post, mock_time, connected_adapter):
        """Latency should be recorded as > 0ms after a successful switch.

        time.monotonic is mocked to return 0.0 (start) then 0.075 (end),
        giving a deterministic 75ms latency regardless of host speed.
        """
        mock_post.return_value = _mock_response({"success": True})
        connected_adapter.switch_playlist("tsv6_idle_loop")
        metrics = connected_adapter.get_metrics()
        assert metrics["pisignage_connected"] is True
        assert metrics["pisignage_current_playlist"] == "tsv6_idle_loop"
        assert metrics["pisignage_total_switches"] == 1
        assert metrics["pisignage_failed_switches"] == 0
        assert metrics["pisignage_last_switch_latency_ms"] > 0


# ── PiSignageHealthMonitor Tests ─────────────────────────────────────────────

class TestPiSignageHealthMonitor:

    def test_initial_state(self, connected_adapter):
        monitor = PiSignageHealthMonitor(adapter=connected_adapter)
        assert monitor.is_down is False
        assert monitor.consecutive_failures == 0

    @patch.object(PiSignageAdapter, "health_check", return_value=False)
    def test_calls_on_server_down_after_threshold(self, mock_health, connected_adapter):
        callback = MagicMock()
        monitor = PiSignageHealthMonitor(
            adapter=connected_adapter,
            failure_threshold=2,
            on_server_down=callback,
        )
        # Simulate health check failures manually
        monitor._adapter = connected_adapter
        for _ in range(3):
            monitor._consecutive_failures += 1
            if monitor._consecutive_failures >= 2 and not monitor._is_down:
                monitor._is_down = True
                callback()
        callback.assert_called_once()
        assert monitor.is_down is True
