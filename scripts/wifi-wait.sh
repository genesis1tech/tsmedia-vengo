#!/bin/bash
# TSV6 WiFi Gate — blocks until connectivity is confirmed.
#
# Used as ExecStartPre in tsv6.service so the main app never starts
# without network.
#
# Boot sequence:
#   1. Check if NM has saved WiFi connections
#   2. If yes, wait up to NM_CONNECT_TIMEOUT for NM to connect
#      (NM needs time to scan, authenticate, get DHCP on cold boot)
#   3. If no saved connections OR NM fails to connect → start provisioning
#   4. Block until connected

set -euo pipefail

# How long to wait for NM to connect a saved network on boot
NM_CONNECT_TIMEOUT=30
POLL_INTERVAL=3
PROVISIONING_SERVICE="tsv6-wifi-provisioning.service"

is_connected() {
    # Method 1: NM global connectivity state (requires default route)
    local state
    state=$(nmcli -t -f CONNECTIVITY general 2>/dev/null || echo "none")
    [ "$state" = "full" ] && return 0

    # Method 2: check wlan0 specifically is connected in NM
    # (handles case where NM connectivity check is disabled/unavailable
    #  or NM is slow to update its global connectivity state after DHCP)
    nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -q "^wlan0:connected$"
}

has_saved_wifi() {
    # Check if NM has any saved WiFi (wireless) connections
    nmcli -t -f TYPE connection show 2>/dev/null | grep -q "802-11-wireless"
}

# ── Already connected? ───────────────────────────────────────────────

if is_connected; then
    echo "wifi-wait: already connected"
    exit 0
fi

# ── Saved WiFi exists → give NM time to connect ─────────────────────

if has_saved_wifi; then
    echo "wifi-wait: saved WiFi found, waiting up to ${NM_CONNECT_TIMEOUT}s for NM to connect..."
    elapsed=0
    while [ "$elapsed" -lt "$NM_CONNECT_TIMEOUT" ]; do
        sleep "$POLL_INTERVAL"
        elapsed=$((elapsed + POLL_INTERVAL))
        if is_connected; then
            echo "wifi-wait: connected after ${elapsed}s"
            exit 0
        fi
        echo "wifi-wait: waiting... (${elapsed}/${NM_CONNECT_TIMEOUT}s)"
    done
    echo "wifi-wait: NM failed to connect within ${NM_CONNECT_TIMEOUT}s"
fi

# ── Not connected — start provisioning ───────────────────────────────

echo "wifi-wait: not connected — starting provisioning"
systemctl start "$PROVISIONING_SERVICE" 2>/dev/null || true

# ── Block until connected ────────────────────────────────────────────

while true; do
    if is_connected; then
        echo "wifi-wait: connected — releasing main service"
        if systemctl is-active --quiet "$PROVISIONING_SERVICE" 2>/dev/null; then
            systemctl stop "$PROVISIONING_SERVICE" 2>/dev/null || true
        fi
        exit 0
    fi
    sleep "$POLL_INTERVAL"
done
