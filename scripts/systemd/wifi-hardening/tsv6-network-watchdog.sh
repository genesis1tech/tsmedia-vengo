#!/bin/bash
# TSV6 Network Watchdog — Layer 2 safety net
#
# Pings two independent DNS servers every 60 seconds.
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
# Ping test — success if EITHER target responds
# ---------------------------------------------------------------------------
ping_test() {
    if /bin/ping -c 2 -W 3 -I "$WIFI_INTERFACE" "$PING_TARGET_1" >/dev/null 2>&1; then
        return 0
    fi
    if /bin/ping -c 2 -W 3 -I "$WIFI_INTERFACE" "$PING_TARGET_2" >/dev/null 2>&1; then
        return 0
    fi
    # Neither target reachable — also try without binding to interface
    # (covers cases where default route is via a different interface)
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
    log info "Starting network watchdog: targets=${PING_TARGET_1},${PING_TARGET_2} iface=${WIFI_INTERFACE} interval=${CHECK_INTERVAL}s max_failures=${MAX_FAILURES}"

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
