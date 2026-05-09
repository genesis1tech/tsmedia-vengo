"""Regression test for the silent-no-move servo bug.

Background: STServo torque is enabled exactly once during _connect.  Several
code paths then disable torque (close_door_with_safety after exhausted
obstruction retries, the obstruction-handler service on its own exit,
explicit disable_servo()).  Position writes to a torque-disabled servo
silently no-op — the SDK call succeeds, ReadMoving returns "not moving"
because nothing is moving, and the function falsely reports success.

The fix is to re-arm torque at the top of every motion entry point and
verify physical position after each move.  These tests pin both halves
in place.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def controller():
    """STServoController with the SDK fully mocked.

    The mock servo "physically" tracks its current position so the tests
    can model torque-on (position changes) vs torque-off (position
    doesn't change) regimes without real hardware.
    """
    with patch("tsv6.hardware.stservo.controller.STSERVO_AVAILABLE", True), \
         patch("tsv6.hardware.stservo.controller.PortHandler") as MockPort, \
         patch("tsv6.hardware.stservo.controller.sms_sts") as MockSdk:

        port = MockPort.return_value
        port.openPort.return_value = True
        port.setBaudRate.return_value = True

        sdk = MockSdk.return_value

        state = {"position": 0, "torque": False}

        def write_pos_ex(_id, position, _speed, _accel):
            if state["torque"]:
                state["position"] = position
            return (0, 0)

        def write1byte(_id, _addr, value):
            state["torque"] = bool(value)
            return (0, 0)

        sdk.WritePosEx.side_effect = write_pos_ex
        sdk.write1ByteTxRx.side_effect = write1byte
        sdk.ping.return_value = (1, 0, 0)
        sdk.ReadPos.side_effect = lambda _id: (state["position"], 0, 0)
        sdk.ReadMoving.side_effect = lambda _id: (0, 0, 0)  # never report moving

        from tsv6.hardware.stservo.controller import STServoController

        ctrl = STServoController(port="/dev/null")
        # _connect runs in __init__ and enables torque. Force the state to
        # the torque-DISABLED regime that triggers the production bug.
        state["torque"] = False
        # Reset any position movement that happened during _connect so each
        # test starts from a known closed position.
        state["position"] = 0
        ctrl._sdk_state = state  # expose for assertions
        yield ctrl


def test_open_door_re_enables_torque_when_disabled(controller):
    """open_door must re-arm torque so a previously-disabled servo moves."""
    assert controller._sdk_state["torque"] is False

    ok = controller.open_door(hold_time=0)

    assert ok is True
    assert controller._sdk_state["torque"] is True, (
        "open_door must re-enable torque before issuing position writes"
    )
    assert controller._sdk_state["position"] == controller.open_position, (
        "Servo must physically reach the open position"
    )


def test_close_door_re_enables_torque_when_disabled(controller):
    """close_door must re-arm torque before commanding motion."""
    # Pre-position to "open" so close has somewhere to move to
    controller._sdk_state["position"] = controller.open_position
    controller._sdk_state["torque"] = False

    ok = controller.close_door(hold_time=0)

    assert ok is True
    assert controller._sdk_state["torque"] is True
    assert controller._sdk_state["position"] == controller.closed_position


def test_close_door_with_safety_re_enables_torque_when_disabled(controller):
    """close_door_with_safety must re-arm torque (obstruction-exhausted path
    leaves torque off; the next call must recover)."""
    controller._sdk_state["position"] = controller.open_position
    controller._sdk_state["torque"] = False

    success, status = controller.close_door_with_safety(
        max_retries=1, retry_delay=0, hold_time=0
    )

    assert success is True, f"expected close success, got status={status}"
    assert controller._sdk_state["torque"] is True


def test_open_door_fails_loudly_when_servo_cannot_move(controller, caplog):
    """If the servo physically can't reach target (e.g. mechanical block),
    open_door must return False and log the mismatch — not silently report
    success."""
    # Override write_pos_ex so torque is on but position never updates
    controller._sdk_state["torque"] = True

    def stuck_write(_id, _position, _speed, _accel):
        return (0, 0)  # accept but don't move

    controller.servo.WritePosEx.side_effect = stuck_write

    with caplog.at_level("ERROR"):
        ok = controller.open_door(hold_time=0)

    assert ok is False, "open_door must report failure when servo doesn't reach target"
    assert any(
        "did not reach target" in record.message
        or "position-write completed but servo at" in record.message
        for record in caplog.records
    ), "expected a diagnostic log when servo failed to move"


def test_missing_adapter_does_not_simulate_success(caplog):
    """Production default must fail loudly when the USB adapter is absent."""
    with patch("tsv6.hardware.stservo.controller.STSERVO_AVAILABLE", True), \
         patch("tsv6.hardware.stservo.controller.PortHandler") as MockPort:

        MockPort.return_value.openPort.return_value = False

        from tsv6.hardware.stservo.controller import STServoController

        ctrl = STServoController(port="/dev/missing")

        with caplog.at_level("ERROR"):
            ok = ctrl.open_door(hold_time=0)

    assert ok is False
    assert ctrl.is_connected is False
    assert any(
        "servo adapter is not connected" in record.message
        or "Failed to open port" in record.message
        for record in caplog.records
    )


def test_reconnects_after_adapter_returns(monkeypatch):
    """A controller created while unplugged should recover on the next command."""
    with patch("tsv6.hardware.stservo.controller.STSERVO_AVAILABLE", True), \
         patch("tsv6.hardware.stservo.controller.PortHandler") as MockPort, \
         patch("tsv6.hardware.stservo.controller.sms_sts") as MockSdk:

        open_attempts = {"count": 0}
        port = MockPort.return_value

        def open_port():
            open_attempts["count"] += 1
            return open_attempts["count"] >= 2

        port.openPort.side_effect = open_port

        sdk = MockSdk.return_value
        state = {"position": 0, "torque": False}

        def write_pos_ex(_id, position, _speed, _accel):
            if state["torque"]:
                state["position"] = position
            return (0, 0)

        sdk.ping.return_value = (1, 0, 0)
        sdk.write1ByteTxRx.side_effect = lambda _id, _addr, value: state.update(torque=bool(value)) or (0, 0)
        sdk.WritePosEx.side_effect = write_pos_ex
        sdk.ReadPos.side_effect = lambda _id: (state["position"], 0, 0)
        sdk.ReadMoving.side_effect = lambda _id: (0, 0, 0)

        monkeypatch.setattr(
            "tsv6.hardware.stservo.controller.STServoController._auto_detect_port",
            lambda self: "/dev/ttyACM0",
        )

        from tsv6.hardware.stservo.controller import STServoController

        ctrl = STServoController()
        assert ctrl.is_connected is False

        ok = ctrl.open_door(hold_time=0)

    assert ok is True
    assert ctrl.is_connected is True
    assert state["position"] == ctrl.open_position
    assert open_attempts["count"] == 2
