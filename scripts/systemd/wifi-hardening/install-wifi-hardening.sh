#!/bin/bash
# TSV6 WiFi Hardening Installer
#
# Installs all WiFi hardening layers:
#   - NetworkManager production config (infinite retries, power save off)
#   - WiFi driver tuning (Broadcom roaming off / Intel power save off)
#   - Hardware watchdog (BCM2835 via systemd)
#   - Network connectivity watchdog files (disabled by default)
#   - WiFi power save off service (belt-and-suspenders)
#   - Persistent journald so prior-boot network failures survive reboot
#   - Disable standalone wpa_supplicant auto-start (NM uses D-Bus activation)
#
# Usage:
#   sudo scripts/systemd/wifi-hardening/install-wifi-hardening.sh
#
# A reboot is REQUIRED after installation for:
#   - Hardware watchdog activation (dtparam=watchdog=on)
#   - WiFi driver parameter (Broadcom roamoff=1 / Intel power_save=0)
#   - systemd RuntimeWatchdogSec

set -euo pipefail

# ---------------------------------------------------------------------------
# Ensure root
# ---------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (sudo)" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=============================================="
echo "  TSV6 WiFi Hardening Installer"
echo "=============================================="
echo ""

# ---------------------------------------------------------------------------
# 1. NetworkManager production config
# ---------------------------------------------------------------------------
echo "[1/9] Installing NetworkManager production config..."
mkdir -p /etc/NetworkManager/conf.d
cp "${SCRIPT_DIR}/99-tsv6-wifi-production.conf" /etc/NetworkManager/conf.d/
echo "  → /etc/NetworkManager/conf.d/99-tsv6-wifi-production.conf"

# ---------------------------------------------------------------------------
# 2. WiFi driver tuning (auto-detect chipset)
# ---------------------------------------------------------------------------
echo "[2/9] Installing WiFi driver tuning..."
mkdir -p /etc/modprobe.d

# Detect WiFi driver for wlan0
WIFI_DRIVER=""
if [[ -L /sys/class/net/wlan0/device/driver ]]; then
    WIFI_DRIVER="$(basename "$(readlink /sys/class/net/wlan0/device/driver)")"
fi

case "$WIFI_DRIVER" in
    iwlwifi)
        echo "  Detected: Intel WiFi (iwlwifi)"
        cp "${SCRIPT_DIR}/tsv6-wifi-intel.conf" /etc/modprobe.d/tsv6-wifi.conf
        echo "  → /etc/modprobe.d/tsv6-wifi.conf (Intel: power_save=0)"
        ;;
    brcmfmac)
        echo "  Detected: Broadcom WiFi (brcmfmac)"
        cp "${SCRIPT_DIR}/tsv6-wifi.conf" /etc/modprobe.d/
        echo "  → /etc/modprobe.d/tsv6-wifi.conf (Broadcom: roamoff=1)"
        ;;
    *)
        echo "  WARNING: Unknown WiFi driver '${WIFI_DRIVER:-none}', installing Broadcom config as default"
        cp "${SCRIPT_DIR}/tsv6-wifi.conf" /etc/modprobe.d/
        echo "  → /etc/modprobe.d/tsv6-wifi.conf (default/Broadcom)"
        ;;
esac

# ---------------------------------------------------------------------------
# 3. Hardware watchdog (systemd)
# ---------------------------------------------------------------------------
echo "[3/9] Installing hardware watchdog config..."
mkdir -p /etc/systemd/system.conf.d
cp "${SCRIPT_DIR}/tsv6-watchdog.conf" /etc/systemd/system.conf.d/
echo "  → /etc/systemd/system.conf.d/tsv6-watchdog.conf"

# ---------------------------------------------------------------------------
# 4. Hardware watchdog (boot config)
# ---------------------------------------------------------------------------
echo "[4/9] Enabling hardware watchdog in boot config..."
BOOT_CONFIG="/boot/firmware/config.txt"
if [[ ! -f "$BOOT_CONFIG" ]]; then
    BOOT_CONFIG="/boot/config.txt"
fi

if [[ -f "$BOOT_CONFIG" ]]; then
    if grep -q "^dtparam=watchdog=on" "$BOOT_CONFIG"; then
        echo "  → Already enabled in ${BOOT_CONFIG}"
    elif grep -q "^#dtparam=watchdog" "$BOOT_CONFIG"; then
        # Uncomment existing line
        sed -i 's/^#dtparam=watchdog.*/dtparam=watchdog=on/' "$BOOT_CONFIG"
        echo "  → Uncommented in ${BOOT_CONFIG}"
    else
        echo "" >> "$BOOT_CONFIG"
        echo "# TSV6: Enable BCM2835 hardware watchdog" >> "$BOOT_CONFIG"
        echo "dtparam=watchdog=on" >> "$BOOT_CONFIG"
        echo "  → Added to ${BOOT_CONFIG}"
    fi
else
    echo "  WARNING: Boot config not found, skipping hardware watchdog"
fi

# ---------------------------------------------------------------------------
# 5. Network watchdog script
# ---------------------------------------------------------------------------
echo "[5/9] Installing network watchdog script..."
install -m 0755 "${SCRIPT_DIR}/tsv6-network-watchdog.sh" /usr/local/bin/tsv6-network-watchdog.sh
echo "  → /usr/local/bin/tsv6-network-watchdog.sh"

# ---------------------------------------------------------------------------
# 6. Network watchdog service
# ---------------------------------------------------------------------------
echo "[6/9] Installing network watchdog service..."
cp "${SCRIPT_DIR}/tsv6-network-watchdog.service" /etc/systemd/system/
echo "  → /etc/systemd/system/tsv6-network-watchdog.service"

# ---------------------------------------------------------------------------
# 7. WiFi power save off service
# ---------------------------------------------------------------------------
echo "[7/9] Installing WiFi power save off service..."
cp "${SCRIPT_DIR}/tsv6-wifi-powersave-off.service" /etc/systemd/system/
echo "  → /etc/systemd/system/tsv6-wifi-powersave-off.service"

# ---------------------------------------------------------------------------
# 8. Persistent journald
# ---------------------------------------------------------------------------
echo "[8/9] Enabling persistent journald..."
mkdir -p /etc/systemd/journald.conf.d
cp "${SCRIPT_DIR}/99-tsv6-persistent.conf" /etc/systemd/journald.conf.d/
systemd-tmpfiles --create --prefix /var/log/journal
systemctl restart systemd-journald
journalctl --flush
echo "  → /etc/systemd/journald.conf.d/99-tsv6-persistent.conf"

# ---------------------------------------------------------------------------
# 9. Disable standalone wpa_supplicant auto-start
# ---------------------------------------------------------------------------
echo "[9/9] Configuring wpa_supplicant service..."
# IMPORTANT: Do NOT mask wpa_supplicant! NetworkManager needs it as a
# backend for WPA authentication via D-Bus activation. Masking it causes
# NM to mark wlan0 as "unavailable" after 5 failed D-Bus activate attempts.
#
# We only disable auto-start of the standalone service, which tries to
# manage all wireless interfaces and conflicts with NM (supplicant-failed,
# reason=3 disconnects). NM will still D-Bus activate wpa_supplicant on
# demand for WPA/WPA2/WPA3 connections.
if systemctl is-masked --quiet wpa_supplicant.service 2>/dev/null; then
    # Fix previous misconfiguration that masked it
    systemctl unmask wpa_supplicant.service
    echo "  → Unmasked wpa_supplicant.service (was incorrectly masked)"
fi

if systemctl is-enabled --quiet wpa_supplicant.service 2>/dev/null; then
    systemctl disable wpa_supplicant.service
    echo "  → Disabled wpa_supplicant.service auto-start (NM uses D-Bus activation)"
else
    echo "  → wpa_supplicant.service auto-start already disabled"
fi

# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------
echo ""
echo "Activating services..."

# Reload systemd to pick up new unit files
systemctl daemon-reload

# Enable non-rebooting support services to start on boot.
systemctl enable tsv6-wifi-powersave-off.service

if [[ "${TSV6_ENABLE_NETWORK_WATCHDOG:-false}" =~ ^(1|true|yes|on)$ ]]; then
    systemctl enable tsv6-network-watchdog.service
    echo "  → tsv6-network-watchdog.service enabled"
else
    systemctl disable tsv6-network-watchdog.service 2>/dev/null || true
    echo "  → tsv6-network-watchdog.service installed but disabled (set TSV6_ENABLE_NETWORK_WATCHDOG=true to enable)"
fi

# Reload NetworkManager to pick up new config
if systemctl is-active --quiet NetworkManager; then
    echo "Reloading NetworkManager..."
    nmcli general reload
    echo "  → NetworkManager config reloaded"

    # conf.d only affects NEW connections; patch existing ones
    echo "Applying autoconnect-retries=0 to existing WiFi connections..."
    while IFS=: read -r name uuid type _rest; do
        if [[ "$type" == *"wireless"* ]]; then
            nmcli connection modify "$uuid" connection.autoconnect-retries 0 2>/dev/null && \
                echo "  → ${name}: autoconnect-retries=0" || \
                echo "  WARNING: Failed to update ${name}"
        fi
    done < <(nmcli -t -f NAME,UUID,TYPE connection show)
else
    echo "  WARNING: NetworkManager not running, config will apply on next start"
fi

# Start the power save off service now (doesn't need reboot)
systemctl start tsv6-wifi-powersave-off.service 2>/dev/null || true

if [[ "${TSV6_ENABLE_NETWORK_WATCHDOG:-false}" =~ ^(1|true|yes|on)$ ]]; then
    systemctl start tsv6-network-watchdog.service 2>/dev/null || true
else
    systemctl stop tsv6-network-watchdog.service 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  Installation Complete"
echo "=============================================="
echo ""
echo "Installed:"
echo "  [NM]  /etc/NetworkManager/conf.d/99-tsv6-wifi-production.conf"
case "$WIFI_DRIVER" in
    iwlwifi)  echo "  [DRV] /etc/modprobe.d/tsv6-wifi.conf (Intel: power_save=0)" ;;
    brcmfmac) echo "  [DRV] /etc/modprobe.d/tsv6-wifi.conf (Broadcom: roamoff=1)" ;;
    *)        echo "  [DRV] /etc/modprobe.d/tsv6-wifi.conf (default)" ;;
esac
echo "  [HW]  /etc/systemd/system.conf.d/tsv6-watchdog.conf"
echo "  [HW]  ${BOOT_CONFIG:-/boot/firmware/config.txt} (dtparam=watchdog=on)"
echo "  [L2]  /usr/local/bin/tsv6-network-watchdog.sh"
echo "  [L2]  /etc/systemd/system/tsv6-network-watchdog.service (disabled by default)"
echo "  [PWR] /etc/systemd/system/tsv6-wifi-powersave-off.service"
echo "  [LOG] /etc/systemd/journald.conf.d/99-tsv6-persistent.conf"
echo "  [WPA] wpa_supplicant.service auto-start disabled (NM D-Bus activates it)"
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  REBOOT REQUIRED for full activation:    ║"
echo "║    sudo reboot                           ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "After reboot, verify with:"
echo "  iw wlan0 get power_save                          # Expected: Power save: off"
echo "  nmcli -f connection.autoconnect-retries connection show \"\$(nmcli -t -f NAME connection show --active | head -1)\""
echo "                                                    # Expected: connection.autoconnect-retries: 0"
echo "  journalctl --list-boots                           # Expected: prior boots retained after reboot"
echo "  systemctl is-enabled tsv6-network-watchdog        # Expected: disabled unless explicitly enabled"
echo "  cat /proc/sys/kernel/watchdog                     # Expected: 1"
echo "  systemctl is-enabled wpa_supplicant               # Expected: disabled (not masked!)"
case "$WIFI_DRIVER" in
    iwlwifi)
        echo "  cat /sys/module/iwlwifi/parameters/power_save    # Expected: N (disabled)"
        ;;
    brcmfmac)
        echo "  cat /sys/module/brcmfmac/parameters/roamoff       # Expected: Y"
        ;;
    *)
        echo "  # Check your WiFi driver parameters manually"
        ;;
esac
