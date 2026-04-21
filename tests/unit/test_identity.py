"""
Unit tests for tsv6.display.identity module.

All tests inject a temporary ``sysfs_root`` directory so no real Pi hardware
is required. The real /proc/cpuinfo and /sys/class/net paths are never read.
"""

import pytest
from pathlib import Path

from tsv6.display.identity import get_player_identity, PlayerIdentity


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_cpuinfo(root: Path, serial: str) -> None:
    """Write a minimal /proc/cpuinfo with the given serial value."""
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    (proc / "cpuinfo").write_text(
        f"Processor\t: ARMv7\nSerial\t\t: {serial}\nModel\t\t: Raspberry Pi 4\n",
        encoding="utf-8",
    )


def _write_mac(root: Path, interface: str, mac: str) -> None:
    """Write a network interface address file under /sys/class/net."""
    iface_dir = root / "sys" / "class" / "net" / interface
    iface_dir.mkdir(parents=True, exist_ok=True)
    (iface_dir / "address").write_text(mac + "\n", encoding="utf-8")


# ── Happy Path ───────────────────────────────────────────────────────────────


class TestGetPlayerIdentityHappyPath:
    """All files present and well-formed."""

    def test_returns_player_identity_instance(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")
        _write_mac(tmp_path, "eth0", "dc:a6:32:01:02:03")
        _write_mac(tmp_path, "wlan0", "dc:a6:32:04:05:06")

        result = get_player_identity(sysfs_root=tmp_path)

        assert isinstance(result, PlayerIdentity)

    def test_cpu_serial_is_full_value(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.cpu_serial == "000000001234abcd"

    def test_device_id_is_last_8_chars_uppercase(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.device_id == "1234ABCD"

    def test_player_name_is_ts_plus_device_id(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.player_name == "TS_1234ABCD"

    def test_eth_mac_returned_correctly(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")
        _write_mac(tmp_path, "eth0", "dc:a6:32:01:02:03")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.eth_mac == "dc:a6:32:01:02:03"

    def test_wlan_mac_returned_correctly(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")
        _write_mac(tmp_path, "wlan0", "b8:27:eb:aa:bb:cc")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.wlan_mac == "b8:27:eb:aa:bb:cc"

    def test_both_macs_returned_when_both_present(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")
        _write_mac(tmp_path, "eth0", "dc:a6:32:01:02:03")
        _write_mac(tmp_path, "wlan0", "b8:27:eb:aa:bb:cc")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.eth_mac == "dc:a6:32:01:02:03"
        assert result.wlan_mac == "b8:27:eb:aa:bb:cc"

    def test_player_identity_is_frozen(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")

        result = get_player_identity(sysfs_root=tmp_path)

        with pytest.raises((AttributeError, TypeError)):
            result.device_id = "XXXXXXXX"  # type: ignore[misc]


# ── Fallback: Missing /proc/cpuinfo ──────────────────────────────────────────


class TestGetPlayerIdentityMissingCpuinfo:
    """Graceful fallback when /proc/cpuinfo is absent."""

    def test_missing_cpuinfo_returns_default_serial(self, tmp_path: Path) -> None:
        # No cpuinfo written — directory does not exist either
        result = get_player_identity(sysfs_root=tmp_path)

        assert result.cpu_serial == "0000"

    def test_missing_cpuinfo_device_id_is_0000(self, tmp_path: Path) -> None:
        result = get_player_identity(sysfs_root=tmp_path)

        assert result.device_id == "0000"

    def test_missing_cpuinfo_player_name_is_ts_0000(self, tmp_path: Path) -> None:
        result = get_player_identity(sysfs_root=tmp_path)

        assert result.player_name == "TS_0000"

    def test_missing_cpuinfo_macs_still_read_if_present(self, tmp_path: Path) -> None:
        _write_mac(tmp_path, "eth0", "dc:a6:32:ff:ee:dd")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.cpu_serial == "0000"
        assert result.eth_mac == "dc:a6:32:ff:ee:dd"


# ── Fallback: Missing eth0 MAC ───────────────────────────────────────────────


class TestGetPlayerIdentityMissingEthMac:
    """eth0 address file absent."""

    def test_missing_eth0_returns_none_for_eth_mac(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")
        _write_mac(tmp_path, "wlan0", "b8:27:eb:aa:bb:cc")
        # eth0 directory not created

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.eth_mac is None

    def test_missing_eth0_does_not_affect_other_fields(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.device_id == "1234ABCD"
        assert result.player_name == "TS_1234ABCD"
        assert result.eth_mac is None


# ── Fallback: Missing wlan0 MAC ──────────────────────────────────────────────


class TestGetPlayerIdentityMissingWlanMac:
    """wlan0 address file absent."""

    def test_missing_wlan0_returns_none_for_wlan_mac(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")
        _write_mac(tmp_path, "eth0", "dc:a6:32:01:02:03")
        # wlan0 directory not created

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.wlan_mac is None

    def test_missing_wlan0_does_not_affect_eth_mac(self, tmp_path: Path) -> None:
        _write_cpuinfo(tmp_path, "000000001234abcd")
        _write_mac(tmp_path, "eth0", "dc:a6:32:01:02:03")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.eth_mac == "dc:a6:32:01:02:03"
        assert result.wlan_mac is None


# ── Serial Line Format Variations ────────────────────────────────────────────


class TestGetPlayerIdentitySerialFormats:
    """Variations in whitespace and letter case in the cpuinfo Serial line."""

    def test_leading_trailing_whitespace_in_serial_value(self, tmp_path: Path) -> None:
        proc = tmp_path / "proc"
        proc.mkdir(parents=True, exist_ok=True)
        (proc / "cpuinfo").write_text(
            "Processor\t: ARMv7\nSerial\t\t:   000000001234abcd   \n",
            encoding="utf-8",
        )

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.cpu_serial == "000000001234abcd"

    def test_uppercase_serial_key(self, tmp_path: Path) -> None:
        """Some Pi firmware uses 'SERIAL' in all-caps."""
        proc = tmp_path / "proc"
        proc.mkdir(parents=True, exist_ok=True)
        (proc / "cpuinfo").write_text(
            "Processor\t: ARMv7\nSERIAL\t\t: 000000001234abcd\n",
            encoding="utf-8",
        )

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.cpu_serial == "000000001234abcd"

    def test_mixed_case_serial_key(self, tmp_path: Path) -> None:
        proc = tmp_path / "proc"
        proc.mkdir(parents=True, exist_ok=True)
        (proc / "cpuinfo").write_text(
            "Processor\t: ARMv7\nSeRiAl\t\t: 000000009999aaaa\n",
            encoding="utf-8",
        )

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.cpu_serial == "000000009999aaaa"
        assert result.device_id == "9999AAAA"

    def test_short_serial_uses_full_value_as_device_id(self, tmp_path: Path) -> None:
        """If serial has fewer than 8 chars, last 8 chars is the full string."""
        _write_cpuinfo(tmp_path, "1234")

        result = get_player_identity(sysfs_root=tmp_path)

        # "1234"[-8:] == "1234" — no error, no truncation
        assert result.device_id == "1234"
        assert result.player_name == "TS_1234"

    def test_mac_trailing_newline_is_stripped(self, tmp_path: Path) -> None:
        """MAC address files typically end with a newline; it must be stripped."""
        _write_cpuinfo(tmp_path, "000000001234abcd")
        iface_dir = tmp_path / "sys" / "class" / "net" / "eth0"
        iface_dir.mkdir(parents=True, exist_ok=True)
        (iface_dir / "address").write_text("dc:a6:32:01:02:03\n", encoding="utf-8")

        result = get_player_identity(sysfs_root=tmp_path)

        assert result.eth_mac == "dc:a6:32:01:02:03"
        assert "\n" not in result.eth_mac
