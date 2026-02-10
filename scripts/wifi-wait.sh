#!/bin/bash
# TSV6 WiFi Gate — blocks until connectivity is confirmed.
#
# Used as ExecStartPre in tsv6.service so the main app never starts
# without network. Based on the Balena wifi-connect pattern:
#   1. Single instant check
#   2. If not connected → start provisioning immediately
#   3. Block until connected
#
# Uses nmcli connectivity (HTTP-based) instead of Balena's iwgetid
# for reliable detection even on networks that block ICMP.

set -euo pipefail

POLL_INTERVAL=3
PROVISIONING_SERVICE="tsv6-wifi-provisioning.service"

is_connected() {
    local state
    state=$(nmcli -t -f CONNECTIVITY general 2>/dev/null || echo "none")
    [ "$state" = "full" ]
}

# ── Single instant check (Balena pattern) ──────────────────────────────

if is_connected; then
    echo "wifi-wait: connected"
    exit 0
fi

# ── Not connected — start provisioning ─────────────────────────────────

echo "wifi-wait: not connected — starting provisioning"
systemctl start "$PROVISIONING_SERVICE" 2>/dev/null || true

# ── Block until connected ──────────────────────────────────────────────

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
