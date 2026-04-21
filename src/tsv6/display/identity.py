"""
Player identity helper for TSV6 kiosk devices.

Reads hardware identity from the Raspberry Pi's /proc/cpuinfo and
/sys/class/net network interfaces to produce a stable ``PlayerIdentity``
that is consistent with the AWS IoT Thing naming convention used
throughout the TSV6 codebase (``TS_<LAST8_OF_SERIAL>``).

This module is a leaf dependency — it does NOT import from tsv6.config.

Usage::

    from tsv6.display.identity import get_player_identity

    identity = get_player_identity()
    print(identity.player_name)   # e.g. "TS_ABCD1234"

Testing::

    # Inject a fake sysfs root so tests never read real hardware files:
    identity = get_player_identity(sysfs_root=tmp_path)
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SERIAL = "0000"


@dataclass(frozen=True)
class PlayerIdentity:
    """Immutable snapshot of this device's hardware identity."""

    cpu_serial: str
    """Full CPU serial string from /proc/cpuinfo."""

    device_id: str
    """Last 8 characters of cpu_serial, uppercased."""

    player_name: str
    """AWS IoT Thing name: ``TS_<device_id>``."""

    eth_mac: str | None
    """Ethernet MAC address, or None if unavailable."""

    wlan_mac: str | None
    """Wireless LAN MAC address, or None if unavailable."""


def _read_cpu_serial(sysfs_root: Path) -> str:
    """Parse /proc/cpuinfo and return the Serial value.

    Returns ``_DEFAULT_SERIAL`` on any read or parse failure so that the
    caller always receives a non-empty string.
    """
    cpuinfo_path = sysfs_root / "proc" / "cpuinfo"
    try:
        text = cpuinfo_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("Cannot read %s: %s", cpuinfo_path, exc)
        return _DEFAULT_SERIAL

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("serial"):
            parts = stripped.split(":", 1)
            if len(parts) == 2:
                serial = parts[1].strip()
                if serial:
                    return serial
    logger.debug("No Serial line found in %s", cpuinfo_path)
    return _DEFAULT_SERIAL


def _read_mac(sysfs_root: Path, interface: str) -> str | None:
    """Read a network interface MAC address from /sys/class/net.

    Returns None if the file is absent or unreadable.
    """
    mac_path = sysfs_root / "sys" / "class" / "net" / interface / "address"
    try:
        return mac_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.debug("Cannot read %s: %s", mac_path, exc)
        return None


def get_player_identity(sysfs_root: Path = Path("/")) -> PlayerIdentity:
    """Build and return the device's ``PlayerIdentity``.

    Args:
        sysfs_root: Root prefix for all sysfs/procfs reads.  Defaults to
            ``Path("/")`` (real hardware).  Pass a ``tmp_path`` directory
            in tests to inject fake files without mocking.

    Returns:
        A frozen ``PlayerIdentity`` dataclass.
    """
    cpu_serial = _read_cpu_serial(sysfs_root)
    device_id = cpu_serial[-8:].upper()
    player_name = f"TS_{device_id}"
    eth_mac = _read_mac(sysfs_root, "eth0")
    wlan_mac = _read_mac(sysfs_root, "wlan0")

    return PlayerIdentity(
        cpu_serial=cpu_serial,
        device_id=device_id,
        player_name=player_name,
        eth_mac=eth_mac,
        wlan_mac=wlan_mac,
    )
