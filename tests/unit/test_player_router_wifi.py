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


def test_device_status_reports_identity_and_wifi(monkeypatch, tmp_path):
    server = _server(tmp_path)

    monkeypatch.setattr(
        router_module,
        "get_player_identity",
        lambda: SimpleNamespace(
            player_name="TS_EFFC94AA",
            cpu_serial="00000000effc94aa",
            device_id="EFFC94AA",
        ),
    )

    def fake_run(cmd, **_kwargs):
        if cmd[:4] == ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY"]:
            return _completed(stdout="yes:G1:82:WPA2\n")
        if cmd[:4] == ["nmcli", "-t", "-f", "IP4.ADDRESS"]:
            return _completed(stdout="IP4.ADDRESS[1]:192.168.1.137/24\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(router_module.subprocess, "run", fake_run)

    response = server._app.test_client().get("/api/device/status")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["device"]["name"] == "TS_EFFC94AA"
    assert body["wifi"]["connected"] is True
    assert body["wifi"]["current"]["ssid"] == "G1"
    assert body["wifi"]["ip"] == "192.168.1.137"


def test_motor_routes_delegate_to_callback(tmp_path):
    server = _server(tmp_path)
    calls = []

    def callback(action, payload):
        calls.append((action, payload))
        return {
            "ok": True,
            "available": True,
            "calibration": {"open_position": 2800, "closed_position": 4070},
        }

    server.set_motor_callback(callback)
    client = server._app.test_client()

    status = client.get("/api/motor/status")
    move = client.post("/api/motor/move", json={"target": "open"})

    assert status.status_code == 200
    assert move.status_code == 200
    assert calls == [("status", {}), ("move", {"target": "open"})]


def test_motor_routes_return_unavailable_without_callback(tmp_path):
    server = _server(tmp_path)

    response = server._app.test_client().get("/api/motor/status")

    assert response.status_code == 503
    body = response.get_json()
    assert body["ok"] is False
    assert body["available"] is False
