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


class _FakeRouter:
    def __init__(self):
        self.callback = None
        self.motor_callback = None

    def set_wake_callback(self, callback):
        self.callback = callback

    def set_motor_callback(self, callback):
        self.motor_callback = callback


class _FakeRenderer:
    def __init__(self, router):
        self._router = router


class _FakeNetworkDeadlineMonitor:
    def __init__(self):
        self.mark_connected_calls = 0

    def mark_connected(self):
        self.mark_connected_calls += 1


def _player(display_backend):
    player = ProductionVideoPlayer.__new__(ProductionVideoPlayer)
    player.aws_manager = _FakeAWSManager(connected=False)
    player.display_backend = display_backend
    player.network_deadline_monitor = _FakeNetworkDeadlineMonitor()
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
    assert player.network_deadline_monitor.mark_connected_calls == 1
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


def test_settings_exit_restarts_idle_display():
    backend = _FakeDisplayBackend(state="vengo_idle")
    player = _player(backend)
    toggle_calls = []
    player._toggle_vlc_window = lambda hide: toggle_calls.append(hide)

    player._resume_from_settings()

    deadline = time.time() + 1
    while time.time() < deadline and backend.show_idle_calls == 0:
        time.sleep(0.01)

    assert toggle_calls == [False]
    assert backend.show_idle_calls == 1


def test_settings_wake_callback_is_wired_without_long_press():
    router = _FakeRouter()
    backend = _FakeDisplayBackend(state="vengo_idle")
    backend._renderer = _FakeRenderer(router)
    player = _player(backend)
    toggle_calls = []
    player._toggle_vlc_window = lambda hide: toggle_calls.append(hide)

    assert player._wire_settings_wake_callback() is True
    assert router.callback.__self__ is player
    assert router.callback.__func__ is ProductionVideoPlayer._resume_from_settings

    router.callback()

    deadline = time.time() + 1
    while time.time() < deadline and backend.show_idle_calls == 0:
        time.sleep(0.01)

    assert toggle_calls == [False]
    assert backend.show_idle_calls == 1


def test_settings_motor_callback_is_wired_without_long_press():
    router = _FakeRouter()
    backend = _FakeDisplayBackend(state="vengo_idle")
    backend._renderer = _FakeRenderer(router)
    player = _player(backend)

    assert player._wire_settings_motor_callback() is True
    assert router.motor_callback.__self__ is player
    assert router.motor_callback.__func__ is ProductionVideoPlayer._handle_motor_setup_command


class _FakeServo:
    def __init__(self):
        self.open_position = 2800
        self.closed_position = 4070
        self.current_position = 100
        self.moves = []
        self.simulation_mode = False
        self.port = "/dev/ttyACM0"
        self.servo_id = 1

    @property
    def is_connected(self):
        return True

    def get_position(self):
        return self.current_position

    def open_door(self, hold_time=0):
        self.moves.append(("open", hold_time))
        self.current_position = self.open_position
        return True

    def close_door(self, hold_time=0):
        self.moves.append(("closed", hold_time))
        self.current_position = self.closed_position
        return True

    def disable_servo(self):
        self.moves.append(("release", 0))
        return True

    def set_calibration(self, open_position=None, closed_position=None, persist=True):
        if open_position is not None:
            self.open_position = open_position
        if closed_position is not None:
            self.closed_position = closed_position
        return self.get_calibration()

    def get_calibration(self):
        return {
            "open_position": self.open_position,
            "closed_position": self.closed_position,
            "current_position": self.current_position,
            "connected": True,
            "simulation": False,
            "port": self.port,
            "servo_id": self.servo_id,
        }


def test_motor_setup_calibration_and_move_commands():
    player = ProductionVideoPlayer.__new__(ProductionVideoPlayer)
    player.servo_controller = _FakeServo()
    player._door_sequence_lock = threading.Lock()
    player._door_sequence_active = False
    player.logger = logging.getLogger("test-production-motor-setup")

    saved = player._handle_motor_setup_command(
        "calibration",
        {"open_position": 3000, "closed_position": 3900},
    )
    moved = player._handle_motor_setup_command("move", {"target": "open"})
    released = player._handle_motor_setup_command("move", {"target": "release"})

    assert saved["ok"] is True
    assert saved["calibration"]["open_position"] == 3000
    assert saved["calibration"]["closed_position"] == 3900
    assert moved["ok"] is True
    assert released["ok"] is True
    assert player.servo_controller.moves == [("open", 0), ("release", 0)]
