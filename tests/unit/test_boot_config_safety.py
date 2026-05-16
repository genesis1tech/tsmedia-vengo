import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "config" / "boot" / "config.txt.golden"

REQUIRED_BOOT_LINES = [
    "dtoverlay=vc4-kms-v3d",
    "dtoverlay=vc4-kms-dsi-waveshare-panel-v2,10_1_inch_a",
    "hdmi_force_hotplug=1",
    "hdmi_group=2",
    "hdmi_mode=82",
    "hdmi_drive=2",
]


def test_golden_boot_config_contains_required_display_settings():
    data = GOLDEN.read_bytes()

    assert data
    assert b"\x00" not in data
    text = data.decode("ascii")
    for line in REQUIRED_BOOT_LINES:
        assert line in text


def test_legacy_scripts_do_not_mutate_boot_config_directly():
    checked_files = [
        ROOT / "setup-pi-config.sh",
        ROOT / "tsv6-pi5-setup.sh",
        ROOT / "gpu-stability-config.sh",
        ROOT / "setup-sim7600.sh",
        ROOT / "scripts" / "enable-hdmi-output.sh",
        ROOT / "scripts" / "systemd" / "wifi-hardening" / "install-wifi-hardening.sh",
        ROOT / "src" / "tsv6" / "hardware" / "display_driver_monitor.py",
    ]
    forbidden_snippets = [
        'sed -i \'/^gpu_mem=/d\' "$CONFIG_FILE"',
        'sudo tee -a "$CONFIG_FILE"',
        'echo "enable_uart=1" >> /boot/firmware/config.txt',
        'echo "enable_uart=1" >> /boot/config.txt',
        "sed -i 's/^#dtparam=watchdog",
        'echo "dtparam=watchdog=on" >> "$BOOT_CONFIG"',
        'subprocess.run([\'sudo\', \'cp\', temp_file, config_file]',
    ]

    for path in checked_files:
        text = path.read_text()
        for snippet in forbidden_snippets:
            assert snippet not in text, f"{path} still contains direct boot config mutation"


def test_boot_config_installer_writes_target_and_last_known_good(tmp_path):
    target = tmp_path / "config.txt"
    backup_dir = tmp_path / "backups"
    env = {
        **os.environ,
        "TSV6_BOOT_CONFIG_PATH": str(target),
        "TSV6_BOOT_CONFIG_BACKUP_DIR": str(backup_dir),
        "TSV6_BOOT_CONFIG_TEMPLATE": str(GOLDEN),
    }

    subprocess.run(
        ["bash", str(ROOT / "scripts" / "install-boot-config.sh")],
        cwd=ROOT,
        env=env,
        check=True,
    )

    assert target.read_text() == GOLDEN.read_text()
    assert (tmp_path / "config.txt.last-known-good").read_text() == GOLDEN.read_text()
    assert (tmp_path / ".metadata_never_index").exists()


def test_boot_config_guard_restores_corrupt_target(tmp_path):
    target = tmp_path / "config.txt"
    last_known_good = tmp_path / "config.txt.last-known-good"
    log_file = tmp_path / "guard.log"
    target.write_bytes(b"\x00" * 32)
    last_known_good.write_text(GOLDEN.read_text())
    env = {
        **os.environ,
        "TSV6_BOOT_CONFIG_PATH": str(target),
        "TSV6_BOOT_CONFIG_LAST_KNOWN_GOOD": str(last_known_good),
        "TSV6_BOOT_CONFIG_GUARD_LOG": str(log_file),
    }

    subprocess.run(
        ["bash", str(ROOT / "scripts" / "boot-config-guard.sh")],
        cwd=ROOT,
        env=env,
        check=True,
    )

    assert target.read_text() == GOLDEN.read_text()
    assert "restored boot config" in log_file.read_text()
