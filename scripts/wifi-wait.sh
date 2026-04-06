#!/bin/bash
# TSV6 Network Gate — blocks until connectivity is confirmed.
#
# Used as ExecStartPre in tsv6.service so the main app never starts
# without network.
#
# Sources /run/tsv6-network-adapter.env (written by switch-network-adapter.sh)
# to determine whether to wait for WiFi or LTE.
#
# Boot sequence:
#   1. Source env file to learn which adapters are enabled
#   2. If LTE-primary (lte_only or lte_primary_wifi_backup):
#      a. Wait for wwan0 connectivity (up to LTE_CONNECT_TIMEOUT)
#      b. Skip WiFi provisioning entirely
#   3. If WiFi-primary (default):
#      a. Check if NM has saved WiFi connections
#      b. If yes, wait up to NM_CONNECT_TIMEOUT for NM to connect
#      c. If no saved connections OR NM fails to connect → start provisioning
#   4. Block until connected

set -euo pipefail

# How long to wait for NM to connect a saved network on boot
NM_CONNECT_TIMEOUT=30
LTE_CONNECT_TIMEOUT=90
POLL_INTERVAL=3
PROVISIONING_SERVICE="tsv6-wifi-provisioning.service"

# ── Source adapter settings from switch-network-adapter.sh ────────────
# This env file is written by the ExecStartPre that runs before us.
ENV_FILE="/run/tsv6-network-adapter.env"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    . "$ENV_FILE"
fi

# Defaults if env file missing or incomplete
TSV6_WIFI_ENABLED="${TSV6_WIFI_ENABLED:-true}"
TSV6_LTE_ENABLED="${TSV6_LTE_ENABLED:-false}"
TSV6_CONNECTIVITY_MODE="${TSV6_CONNECTIVITY_MODE:-wifi_only}"

is_connected() {
    # Method 1: NM global connectivity state (covers both WiFi and LTE)
    local state
    state=$(nmcli -t -f CONNECTIVITY general 2>/dev/null || echo "none")
    [ "$state" = "full" ] && return 0

    # Method 2: check wlan0 specifically (WiFi)
    if [ "$TSV6_WIFI_ENABLED" = "true" ]; then
        nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -q "^wlan0:connected$" && return 0
    fi

    # Method 3: check wwan0 specifically (LTE)
    if [ "$TSV6_LTE_ENABLED" = "true" ]; then
        nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -q "^wwan0:connected$" && return 0
    fi

    return 1
}

has_saved_wifi() {
    # Check if NM has any saved WiFi (wireless) connections
    nmcli -t -f TYPE connection show 2>/dev/null | grep -q "802-11-wireless"
}

is_lte_primary() {
    case "$TSV6_CONNECTIVITY_MODE" in
        lte_only|lte_primary_wifi_backup) return 0 ;;
        *) return 1 ;;
    esac
}

# ── Already connected? ───────────────────────────────────────────────

if is_connected; then
    echo "wifi-wait: already connected"
    exit 0
fi

# ── LTE-primary path ─────────────────────────────────────────────────

if is_lte_primary; then
    echo "wifi-wait: LTE-primary mode ($TSV6_CONNECTIVITY_MODE) — waiting up to ${LTE_CONNECT_TIMEOUT}s for LTE..."
    elapsed=0
    while [ "$elapsed" -lt "$LTE_CONNECT_TIMEOUT" ]; do
        sleep "$POLL_INTERVAL"
        elapsed=$((elapsed + POLL_INTERVAL))
        if is_connected; then
            echo "wifi-wait: LTE connected after ${elapsed}s"
            exit 0
        fi
        echo "wifi-wait: waiting for LTE... (${elapsed}/${LTE_CONNECT_TIMEOUT}s)"
    done
    echo "wifi-wait: LTE failed to connect within ${LTE_CONNECT_TIMEOUT}s"

    # If WiFi is also enabled, fall back to WiFi path below
    if [ "$TSV6_WIFI_ENABLED" = "true" ]; then
        echo "wifi-wait: falling back to WiFi..."
    else
        echo "wifi-wait: WiFi disabled — continuing without network"
        exit 0
    fi
fi

# ── WiFi-primary path ────────────────────────────────────────────────

if [ "$TSV6_WIFI_ENABLED" != "true" ]; then
    echo "wifi-wait: WiFi disabled and not LTE-primary — continuing"
    exit 0
fi

# Saved WiFi exists → give NM time to connect
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

# ── Not connected — start WiFi provisioning ──────────────────────────

echo "wifi-wait: not connected — starting WiFi provisioning"
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
