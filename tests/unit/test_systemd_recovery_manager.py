from subprocess import CompletedProcess
from unittest.mock import patch

from src.tsv6.utils.systemd_recovery_manager import SystemdRecoveryManager


def test_systemctl_reboot_fallback_returns_true_when_accepted():
    manager = SystemdRecoveryManager()

    with patch(
        "src.tsv6.utils.systemd_recovery_manager.subprocess.run",
        return_value=CompletedProcess(["systemctl", "reboot"], 0, "", ""),
    ) as run:
        assert manager._execute_systemctl_reboot_fallback() is True

    run.assert_called_once_with(
        ["systemctl", "reboot"],
        timeout=5,
        check=False,
        capture_output=True,
        text=True,
    )


def test_systemctl_reboot_fallback_returns_false_when_auth_required():
    manager = SystemdRecoveryManager()

    with patch(
        "src.tsv6.utils.systemd_recovery_manager.subprocess.run",
        return_value=CompletedProcess(
            ["systemctl", "reboot"],
            1,
            "",
            "Interactive authentication required.",
        ),
    ):
        assert manager._execute_systemctl_reboot_fallback() is False
