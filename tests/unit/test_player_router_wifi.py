from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tsv6.display.tsv6_player import router as router_module
from tsv6.display.tsv6_player.router import RouterServer


def _server(tmp_path: Path) -> RouterServer:
    layout = tmp_path / "router_page.html"
    layout.write_text("<html></html>")
    cache = tmp_path / "cache"
    cache.mkdir()
    return RouterServer(cache_dir=cache, layout_html=layout)


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_wifi_connect_active_ssid_does_not_reactivate_profile(monkeypatch, tmp_path):
    server = _server(tmp_path)
    stabilized: list[str] = []

    monkeypatch.setattr(RouterServer, "_saved_wifi_profiles", staticmethod(lambda: {"G1": "G1"}))
    monkeypatch.setattr(RouterServer, "_current_wifi_ssid", staticmethod(lambda: "G1"))
    monkeypatch.setattr(
        RouterServer,
        "_stabilize_selected_wifi_profile",
        staticmethod(lambda profile: stabilized.append(profile) or []),
    )

    def fail_run(*_args, **_kwargs):
        raise AssertionError("active SSID should not call nmcli connection up")

    monkeypatch.setattr(router_module.subprocess, "run", fail_run)

    response = server._app.test_client().post(
        "/api/wifi/connect",
        json={"ssid": "G1", "use_saved": True},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert response.get_json()["already_connected"] is True
    assert stabilized == ["G1"]


def test_wifi_connect_success_stabilizes_selected_profile(monkeypatch, tmp_path):
    server = _server(tmp_path)
    commands: list[list[str]] = []
    stabilized: list[str] = []

    monkeypatch.setattr(RouterServer, "_saved_wifi_profiles", staticmethod(lambda: {"G1": "G1"}))
    monkeypatch.setattr(RouterServer, "_current_wifi_ssid", staticmethod(lambda: ""))
    monkeypatch.setattr(
        RouterServer,
        "_stabilize_selected_wifi_profile",
        staticmethod(lambda profile: stabilized.append(profile) or []),
    )

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        return _completed(stdout="activated")

    monkeypatch.setattr(router_module.subprocess, "run", fake_run)

    response = server._app.test_client().post(
        "/api/wifi/connect",
        json={"ssid": "G1", "use_saved": True},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert ["nmcli", "connection", "up", "G1"] in commands
    assert stabilized == ["G1"]


def test_stabilize_selected_profile_demotes_competing_positive_priorities(monkeypatch):
    commands: list[list[str]] = []

    monkeypatch.setattr(
        RouterServer,
        "_saved_wifi_profile_names",
        staticmethod(lambda: ["selected", "old-positive", "old-negative"]),
    )
    monkeypatch.setattr(
        RouterServer,
        "_profile_autoconnect_priority",
        staticmethod(lambda profile: {"old-positive": 200, "old-negative": -100}.get(profile, 0)),
    )

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        return _completed()

    monkeypatch.setattr(router_module.subprocess, "run", fake_run)

    warnings = RouterServer._stabilize_selected_wifi_profile("selected")

    assert warnings == []
    assert [
        "nmcli", "connection", "modify", "selected",
        "connection.autoconnect", "yes",
        "connection.autoconnect-priority", "300",
        "connection.autoconnect-retries", "0",
        "802-11-wireless.powersave", "2",
        "ipv4.route-metric", "600",
    ] in commands
    assert [
        "nmcli", "connection", "modify", "old-positive",
        "connection.autoconnect-priority", "0",
    ] in commands
    assert all("old-negative" not in cmd for cmd in commands)


def test_wifi_connect_failure_returns_clear_error(monkeypatch, tmp_path):
    server = _server(tmp_path)

    monkeypatch.setattr(RouterServer, "_saved_wifi_profiles", staticmethod(lambda: {"G1": "G1"}))
    monkeypatch.setattr(RouterServer, "_current_wifi_ssid", staticmethod(lambda: ""))

    def fake_run(cmd, **_kwargs):
        assert cmd == ["nmcli", "connection", "up", "G1"]
        return _completed(returncode=10, stderr="activation failed")

    monkeypatch.setattr(router_module.subprocess, "run", fake_run)

    response = server._app.test_client().post(
        "/api/wifi/connect",
        json={"ssid": "G1", "use_saved": True},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is False
    assert body["message"] == "activation failed"
