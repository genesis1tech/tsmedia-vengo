#!/bin/bash
# TSV6 Network Watchdog — Layer 2 safety net
#
# Verifies network connectivity every 60 seconds using a tiered strategy:
#   1. Ping the default gateway (always works, even on networks that block
#      outbound ICMP like university/enterprise WiFi)
#   2. Ping configurable external targets (8.8.8.8, 1.1.1.1) for full
#      internet reachability
#
# Feeds the systemd watchdog on success or during a grace period.
# After sustained failure (>3 consecutive misses), STOPS feeding
# the watchdog so systemd's WatchdogSec=120 expires and
# FailureAction=reboot-force reboots the device.
#
# This script is independent of the Python application.
# If the Python process crashes or hangs, this script still runs.
#
# Configuration: /etc/default/tsv6-network-watchdog (optional)

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults (overridable via EnvironmentFile)
# ---------------------------------------------------------------------------
PING_TARGET_1="${PING_TARGET_1:-8.8.8.8}"
PING_TARGET_2="${PING_TARGET_2:-1.1.1.1}"
WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
CHECK_INTERVAL="${CHECK_INTERVAL:-60}"
MAX_FAILURES="${MAX_FAILURES:-3}"

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
failure_count=0

log() {
    logger -t tsv6-network-watchdog -p "daemon.$1" "$2"
}

# ---------------------------------------------------------------------------
# Get the default gateway for our WiFi interface
# ---------------------------------------------------------------------------
get_gateway() {
    ip route show default dev "$WIFI_INTERFACE" 2>/dev/null | awk '/default/{print $3; exit}'
}

# ---------------------------------------------------------------------------
# Ping test — tiered: gateway first, then external targets
#
# Many enterprise/university networks (e.g. UNCC CoLab) block outbound
# ICMP to external hosts.  Pinging the gateway proves the local network
# link is up without depending on external ICMP policy.
# ---------------------------------------------------------------------------
ping_test() {
    # Tier 1: ping the default gateway (proves local link is up)
    local gw
    gw="$(get_gateway)"
    if [[ -n "$gw" ]]; then
        if /bin/ping -c 2 -W 3 -I "$WIFI_INTERFACE" "$gw" >/dev/null 2>&1; then
            return 0
        fi
    fi

    # Tier 2: ping external targets (proves full internet reachability)
    if /bin/ping -c 2 -W 3 -I "$WIFI_INTERFACE" "$PING_TARGET_1" >/dev/null 2>&1; then
        return 0
    fi
    if /bin/ping -c 2 -W 3 -I "$WIFI_INTERFACE" "$PING_TARGET_2" >/dev/null 2>&1; then
        return 0
    fi
    # Fallback: try without binding to interface
    if /bin/ping -c 2 -W 3 "$PING_TARGET_1" >/dev/null 2>&1; then
        return 0
    fi
    if /bin/ping -c 2 -W 3 "$PING_TARGET_2" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local gw_at_start
    gw_at_start="$(get_gateway)"
    log info "Starting network watchdog: gateway=${gw_at_start:-none} targets=${PING_TARGET_1},${PING_TARGET_2} iface=${WIFI_INTERFACE} interval=${CHECK_INTERVAL}s max_failures=${MAX_FAILURES}"

    # Tell systemd we are ready (Type=notify)
    systemd-notify --ready --status="Monitoring network connectivity"

    while true; do
        if ping_test; then
            # Network is reachable — reset counter, feed watchdog
            if (( failure_count > 0 )); then
                log info "Network recovered after ${failure_count} failure(s)"
            fi
            failure_count=0
            systemd-notify WATCHDOG=1
            systemd-notify --status="OK: network reachable (${PING_TARGET_1}/${PING_TARGET_2})"
        else
            (( failure_count++ )) || true

            if (( failure_count <= MAX_FAILURES )); then
                # Grace period — still feed watchdog to give NetworkManager time
                log warning "Ping failed (${failure_count}/${MAX_FAILURES}) — grace period, still feeding watchdog"
                systemd-notify WATCHDOG=1
                systemd-notify --status="DEGRADED: ping failed ${failure_count}/${MAX_FAILURES} (grace period)"
            else
                # Grace period exhausted — STOP feeding watchdog
                log crit "Ping failed (${failure_count}/${MAX_FAILURES}) — STOPPED feeding watchdog, reboot imminent"
                systemd-notify --status="CRITICAL: network unreachable for ${failure_count} checks, watchdog will expire"
                # Do NOT call systemd-notify WATCHDOG=1 here
            fi
        fi

        sleep "$CHECK_INTERVAL"
    done
}

# Clean shutdown
trap 'log info "Network watchdog stopping"; exit 0' TERM INT

main "$@"
