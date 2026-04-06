#!/bin/bash
# ============================================================================
# TSV6 Network Adapter Switch
# ============================================================================
# Configures which network adapters are active on the device.
# Reads from tsv6.service environment variables:
#
#   TSV6_WIFI_ENABLED=true|false    — Enable onboard WiFi
#   TSV6_LTE_ENABLED=true|false     — Enable 4G LTE modem
#   TSV6_NETWORK_ADAPTER            — Legacy single-adapter mode (still supported)
#   TSV6_CONNECTIVITY_MODE          — Failover strategy
#
# Conflict prevention:
#   - When both WiFi and LTE are enabled, route metrics ensure only one
#     carries traffic at a time (WiFi=600, LTE=100 → LTE preferred).
#   - The connectivity manager in Python handles failover logic.
#   - This script only enables/disables the hardware interfaces.
#
# Called as ExecStartPre in tsv6.service (runs as root via sudo).
# Uses runtime methods (modprobe/rfkill/nmcli) — no reboot required.
# ============================================================================

set -euo pipefail

# Read settings — new dual-toggle model takes precedence over legacy adapter mode
WIFI_ENABLED="${TSV6_WIFI_ENABLED:-true}"
LTE_ENABLED="${TSV6_LTE_ENABLED:-false}"
ADAPTER="${TSV6_NETWORK_ADAPTER:-rpi-wifi}"
CONNECTIVITY_MODE="${TSV6_CONNECTIVITY_MODE:-wifi_only}"
ENV_FILE="/run/tsv6-network-adapter.env"

log() {
    echo "[switch-network-adapter] $*"
}

# Write derived environment variables for the application
write_env_file() {
    log "Writing $ENV_FILE: WIFI_ENABLED=$WIFI_ENABLED, LTE_ENABLED=$LTE_ENABLED, CONNECTIVITY_MODE=$CONNECTIVITY_MODE"
    cat > "$ENV_FILE" <<EOF
TSV6_WIFI_ENABLED=$WIFI_ENABLED
TSV6_LTE_ENABLED=$LTE_ENABLED
TSV6_CONNECTIVITY_MODE=$CONNECTIVITY_MODE
EOF
    chmod 644 "$ENV_FILE"
}

disable_intel_wifi() {
    log "Disabling Intel AX210 (unloading iwlmvm/iwlwifi)..."
    modprobe -r iwlmvm 2>/dev/null || true
    modprobe -r iwlwifi 2>/dev/null || true
}

enable_broadcom_wifi() {
    log "Enabling onboard Broadcom WiFi (loading brcmfmac)..."
    modprobe brcmfmac 2>/dev/null || true
    nmcli radio wifi on 2>/dev/null || true
    sleep 2  # Wait for interface to appear
    # Enforce WiFi route metric 600 so LTE (metric 100) wins default route
    # when both interfaces are active during failover transitions
    local wifi_conn
    wifi_conn=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | grep ":wlan0" | head -1 | cut -d: -f1)
    if [ -n "$wifi_conn" ]; then
        nmcli connection modify "$wifi_conn" ipv4.route-metric 600 2>/dev/null || true
        log "WiFi route metric set to 600 (connection: $wifi_conn)"
    fi
}

disable_broadcom_wifi() {
    log "Disabling onboard Broadcom WiFi..."
    nmcli radio wifi off 2>/dev/null || true
}

enable_lte() {
    log "Enabling LTE modem connection..."
    # Ensure ModemManager is running
    systemctl start ModemManager 2>/dev/null || true
    # Don't auto-connect here — the connectivity manager handles that
    log "LTE modem enabled (connection managed by connectivity manager)"
}

disable_lte() {
    log "Disabling LTE modem connection..."
    nmcli connection down hologram-lte 2>/dev/null || true
}

# ── Main logic ───────────────────────────────────────────────────────────────

log "Config: WIFI_ENABLED=$WIFI_ENABLED, LTE_ENABLED=$LTE_ENABLED, ADAPTER=$ADAPTER, MODE=$CONNECTIVITY_MODE"

# Always disable Intel WiFi (not used in current fleet)
disable_intel_wifi

# Handle WiFi
if [ "$WIFI_ENABLED" = "true" ]; then
    if [ "$ADAPTER" = "intel-ax210" ]; then
        log "Adapter mode: intel-ax210 (PCIe Intel AX210)"
        disable_broadcom_wifi
        log "Enabling Intel AX210 (loading iwlwifi)..."
        modprobe iwlwifi 2>/dev/null || true
        nmcli radio wifi on 2>/dev/null || true
        sleep 2
    else
        enable_broadcom_wifi
    fi
else
    disable_broadcom_wifi
fi

# Handle LTE
if [ "$LTE_ENABLED" = "true" ]; then
    enable_lte
else
    disable_lte
fi

# Validate: at least one interface must be enabled
if [ "$WIFI_ENABLED" != "true" ] && [ "$LTE_ENABLED" != "true" ]; then
    log "WARNING: Both WiFi and LTE are disabled! Falling back to WiFi."
    WIFI_ENABLED="true"
    CONNECTIVITY_MODE="wifi_only"
    enable_broadcom_wifi
fi

# Write the resolved settings for the Python application
write_env_file

log "Done."
