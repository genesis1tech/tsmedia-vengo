import logging
import threading
import time

from tsv6.core.production_main import ProductionVideoPlayer


class _FakeAWSManager:
    def __init__(self, connected=False):
        self.connected = connected
        self.start_auto_reconnect_calls = 0

    def start_auto_reconnect(self):
        self.start_auto_reconnect_calls += 1


class _FakeDisplayBackend:
    def __init__(self, state="vengo_idle"):
        self.state = state
        self.show_idle_calls = 0

    def get_metrics(self):
        return {"renderer_state": self.state}

    def show_idle(self):
        self.show_idle_calls += 1
        return True


def _player(display_backend):
    player = ProductionVideoPlayer.__new__(ProductionVideoPlayer)
    player.aws_manager = _FakeAWSManager(connected=False)
    player.display_backend = display_backend
    player._vengo_reconnect_lock = threading.Lock()
    player._last_vengo_reconnect_restart_at = 0.0
    player.logger = logging.getLogger("test-production-network-reconnect")
    return player


def test_network_reconnect_restarts_vengo_idle(monkeypatch):
    monkeypatch.setenv("TSV6_VENGO_RECONNECT_RESTART_DELAY_SECS", "0")
    backend = _FakeDisplayBackend(state="vengo_idle")
    player = _player(backend)

    player._on_network_reconnect({"connectivity_ok": True})

    deadline = time.time() + 1
    while time.time() < deadline and backend.show_idle_calls == 0:
        time.sleep(0.01)

    assert player.aws_manager.start_auto_reconnect_calls == 1
    assert backend.show_idle_calls == 1


def test_network_reconnect_does_not_interrupt_product_display(monkeypatch):
    monkeypatch.setenv("TSV6_VENGO_RECONNECT_RESTART_DELAY_SECS", "0")
    backend = _FakeDisplayBackend(state="product")
    player = _player(backend)

    player._on_network_reconnect({"connectivity_ok": True})

    time.sleep(0.05)

    assert backend.show_idle_calls == 0


def test_network_reconnect_restart_is_debounced(monkeypatch):
    monkeypatch.setenv("TSV6_VENGO_RECONNECT_RESTART_DELAY_SECS", "0")
    backend = _FakeDisplayBackend(state="vengo_idle")
    player = _player(backend)

    player._on_network_reconnect({"connectivity_ok": True})
    player._on_network_reconnect({"connectivity_ok": True})

    deadline = time.time() + 1
    while time.time() < deadline and backend.show_idle_calls == 0:
        time.sleep(0.01)

    time.sleep(0.05)

    assert backend.show_idle_calls == 1
