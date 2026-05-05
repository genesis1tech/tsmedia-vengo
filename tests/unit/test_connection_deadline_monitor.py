import time
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

from src.tsv6.utils.connection_tracker import ConnectionDeadlineMonitor


def test_deadline_monitor_uses_connection_name_in_thread_name():
    monitor = ConnectionDeadlineMonitor(
        disconnection_deadline_minutes=1,
        connection_name="Network",
        enable_forced_reboot=False,
    )

    monitor.start()
    try:
        assert monitor._monitor_thread is not None
        assert monitor._monitor_thread.name == "NetworkConnectionDeadlineMonitor"
    finally:
        monitor.stop()


def test_deadline_monitor_requests_systemd_reboot_when_enabled():
    recovery = Mock()
    recovery.execute_system_reboot.return_value = True
    callback = Mock()
    monitor = ConnectionDeadlineMonitor(
        disconnection_deadline_minutes=1,
        on_deadline_exceeded=callback,
        enable_forced_reboot=True,
        systemd_recovery_manager=recovery,
        connection_name="Network",
        reboot_reason="network unreachable past configured deadline",
    )
    monitor.disconnected_since = time.time() - 90

    with patch("src.tsv6.utils.connection_tracker.subprocess.run") as run:
        monitor._handle_deadline_exceeded()

    callback.assert_called_once()
    recovery.execute_system_reboot.assert_called_once()
    run.assert_called_once_with(["sync"], timeout=10)
    assert monitor.deadline_exceeded is True


def test_deadline_monitor_does_not_reboot_when_disabled():
    recovery = Mock()
    monitor = ConnectionDeadlineMonitor(
        disconnection_deadline_minutes=1,
        enable_forced_reboot=False,
        systemd_recovery_manager=recovery,
        connection_name="Network",
    )
    monitor.disconnected_since = time.time() - 90

    monitor._handle_deadline_exceeded()

    recovery.execute_system_reboot.assert_not_called()
    assert monitor.deadline_exceeded is True


def test_deadline_monitor_tries_sudo_reboot_when_systemd_manager_fails():
    recovery = Mock()
    recovery.execute_system_reboot.return_value = False
    monitor = ConnectionDeadlineMonitor(
        disconnection_deadline_minutes=1,
        enable_forced_reboot=True,
        systemd_recovery_manager=recovery,
        connection_name="Network",
    )
    monitor.disconnected_since = time.time() - 90

    results = [
        CompletedProcess(["sync"], 0),
        CompletedProcess(["systemctl", "reboot"], 1),
        CompletedProcess(["sudo", "-n", "systemctl", "reboot"], 0),
    ]

    with patch("src.tsv6.utils.connection_tracker.time.sleep"), patch(
        "src.tsv6.utils.connection_tracker.subprocess.run",
        side_effect=results,
    ) as run:
        monitor._handle_deadline_exceeded()

    recovery.execute_system_reboot.assert_called_once()
    assert run.call_args_list[0].args[0] == ["sync"]
    assert run.call_args_list[1].args[0] == ["systemctl", "reboot"]
    assert run.call_args_list[2].args[0] == ["sudo", "-n", "systemctl", "reboot"]
