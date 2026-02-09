#!/bin/bash
# ============================================================================
# TSV6 Network Adapter Switch
# ============================================================================
# Configures which network adapter is active on the device.
# Only one adapter is enabled at a time.
#
# Reads: TSV6_NETWORK_ADAPTER environment variable
#   rpi-wifi     (default) - Onboard Broadcom WiFi
#   intel-ax210            - PCIe Intel AX210 WiFi 6E card
#   4g-lte                 - SIM7600NA-H 4G LTE modem
#
# Called as ExecStartPre in tsv6.service (runs as root via sudo).
# Uses runtime methods (modprobe/rfkill/nmcli) — no reboot required.
# ============================================================================

set -euo pipefail

ADAPTER="${TSV6_NETWORK_ADAPTER:-rpi-wifi}"
ENV_FILE="/run/tsv6-network-adapter.env"

log() {
    echo "[switch-network-adapter] $*"
}

# Write derived environment variables for the application
write_env_file() {
    local lte_enabled="$1"
    local connectivity_mode="$2"
    log "Writing $ENV_FILE: LTE_ENABLED=$lte_enabled, CONNECTIVITY_MODE=$connectivity_mode"
    cat > "$ENV_FILE" <<EOF
TSV6_LTE_ENABLED=$lte_enabled
TSV6_CONNECTIVITY_MODE=$connectivity_mode
EOF
    chmod 644 "$ENV_FILE"
}

disable_intel_wifi() {
    log "Disabling Intel AX210 (unloading iwlmvm/iwlwifi)..."
    modprobe -r iwlmvm 2>/dev/null || true
    modprobe -r iwlwifi 2>/dev/null || true
}

enable_intel_wifi() {
    log "Enabling Intel AX210 (loading iwlwifi)..."
    modprobe iwlwifi 2>/dev/null || true
    # iwlmvm loads automatically as dependency
    sleep 2  # Wait for interface to appear
}

disable_broadcom_wifi() {
    log "Disabling onboard Broadcom WiFi (unloading brcmfmac)..."
    modprobe -r brcmfmac 2>/dev/null || true
}

enable_broadcom_wifi() {
    log "Enabling onboard Broadcom WiFi (loading brcmfmac)..."
    modprobe brcmfmac 2>/dev/null || true
    sleep 2  # Wait for interface to appear
}

disable_lte() {
    log "Disabling LTE modem connection..."
    nmcli connection down hologram-lte 2>/dev/null || true
}

enable_wifi_radio() {
    log "Enabling WiFi radio..."
    nmcli radio wifi on 2>/dev/null || true
}

disable_wifi_radio() {
    log "Disabling WiFi radio..."
    nmcli radio wifi off 2>/dev/null || true
}

case "$ADAPTER" in
    rpi-wifi)
        log "Adapter mode: rpi-wifi (onboard Broadcom)"
        disable_intel_wifi
        enable_broadcom_wifi
        enable_wifi_radio
        disable_lte
        write_env_file "false" "wifi_only"
        log "Onboard Broadcom WiFi active"
        ;;

    intel-ax210)
        log "Adapter mode: intel-ax210 (PCIe Intel AX210)"
        disable_broadcom_wifi
        enable_intel_wifi
        enable_wifi_radio
        disable_lte
        write_env_file "false" "wifi_only"
        log "Intel AX210 WiFi active"
        ;;

    4g-lte)
        log "Adapter mode: 4g-lte (SIM7600 LTE modem)"
        disable_intel_wifi
        disable_broadcom_wifi
        disable_wifi_radio
        write_env_file "true" "lte_only"
        log "4G LTE modem active (WiFi disabled)"
        ;;

    *)
        log "ERROR: Unknown adapter '$ADAPTER'. Valid: rpi-wifi, intel-ax210, 4g-lte"
        log "Falling back to rpi-wifi"
        disable_intel_wifi
        enable_broadcom_wifi
        enable_wifi_radio
        disable_lte
        write_env_file "false" "wifi_only"
        ;;
esac

log "Done."
