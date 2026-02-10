#!/bin/bash
# TSV6 WiFi Gate — blocks until WiFi (or wired) connectivity is confirmed.
#
# Used as ExecStartPre in tsv6.service so the main app never starts
# without network.  If no WiFi is saved, kicks off the provisioning
# service and waits for the user to configure it.
#
# Based on the Balena wifi-connect pattern (industry standard for IoT).

set -euo pipefail

POLL_INTERVAL=3            # seconds between connectivity checks
PROVISIONING_SERVICE="tsv6-wifi-provisioning.service"

# ── Helpers ──────────────────────────────────────────────────────────────

is_connected() {
    # Check if NetworkManager reports full connectivity.
    # "full" means: IP assigned, gateway reachable, internet check passed.
    local state
    state=$(nmcli -t -f CONNECTIVITY general 2>/dev/null || echo "none")
    [ "$state" = "full" ]
}

has_wifi_saved() {
    # Check if any WiFi connection profiles exist in NetworkManager.
    nmcli -t -f TYPE connection show 2>/dev/null | grep -q "802-11-wireless"
}

is_provisioning_active() {
    systemctl is-active --quiet "$PROVISIONING_SERVICE" 2>/dev/null
}

# ── Fast path ────────────────────────────────────────────────────────────

if is_connected; then
    echo "wifi-wait: already connected"
    exit 0
fi

# Give NetworkManager a moment to auto-connect on boot
echo "wifi-wait: waiting for NetworkManager auto-connect..."
for i in $(seq 1 10); do
    sleep 2
    if is_connected; then
        echo "wifi-wait: connected after ${i}x2s"
        exit 0
    fi
done

# ── No connection yet — start provisioning if needed ─────────────────────

if ! has_wifi_saved; then
    echo "wifi-wait: no saved WiFi — starting provisioning"
    systemctl start "$PROVISIONING_SERVICE" 2>/dev/null || true
elif ! is_provisioning_active; then
    echo "wifi-wait: saved WiFi not connecting — starting provisioning"
    systemctl start "$PROVISIONING_SERVICE" 2>/dev/null || true
fi

# ── Block until connected ────────────────────────────────────────────────

echo "wifi-wait: blocking until connectivity confirmed..."
while true; do
    if is_connected; then
        echo "wifi-wait: connected — releasing main service"
        # Stop provisioning if it's still running (AP teardown handled by service)
        if is_provisioning_active; then
            systemctl stop "$PROVISIONING_SERVICE" 2>/dev/null || true
        fi
        exit 0
    fi
    sleep "$POLL_INTERVAL"
done
