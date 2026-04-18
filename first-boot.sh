#!/bin/bash
################################################################################
# TSV6 First-Boot Provisioning Script
#
# Runs automatically on first boot of a golden-image SD card to provision
# device-specific configuration (AWS IoT certs, media, hostname).
#
# This script is idempotent — safe to run multiple times.
#
# Golden image workflow:
#   1. Flash base Raspberry Pi OS to SD card
#   2. Clone the repo + run deploy.sh --skip-aws --skip-media on one reference Pi
#   3. Image that SD card as the "golden image"
#   4. Flash golden image to hundreds of SD cards
#   5. Each device runs first-boot.sh on first power-on
#      (triggered by systemd service or rc.local)
#
# Setup:
#   To auto-run on first boot, deploy.sh installs a systemd service:
#     sudo systemctl enable tsv6-first-boot.service
#
#   Or add to /etc/rc.local:
#     /home/<user>/tsrpi5/first-boot.sh --non-interactive &
################################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="${SUDO_USER:-$USER}"
MARKER_FILE="$SCRIPT_DIR/.first-boot-complete"
LOG_FILE="$SCRIPT_DIR/logs/first-boot-$(date +%Y%m%d_%H%M%S).log"
NON_INTERACTIVE=false

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

mkdir -p "$SCRIPT_DIR/logs"

log()     { echo -e "${GREEN}[$(date +'%H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"; }
error()   { echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"; }
warning() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }
info()    { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[OK]${NC} $1" | tee -a "$LOG_FILE"; }

for arg in "$@"; do
    case "$arg" in
        --non-interactive) NON_INTERACTIVE=true ;;
        --force) rm -f "$MARKER_FILE" ;;
    esac
done

# ── Idempotency check ────────────────────────────────────────────────────────

if [ -f "$MARKER_FILE" ]; then
    info "First-boot provisioning already completed ($(cat "$MARKER_FILE"))"
    info "Use --force to re-run"
    exit 0
fi

log "TSV6 First-Boot Provisioning starting..."

# ── Get device identity ──────────────────────────────────────────────────────

get_device_serial() {
    local serial=""
    if [ -f /proc/cpuinfo ]; then
        serial=$(grep -i "serial" /proc/cpuinfo | cut -d: -f2 | tr -d ' \t')
    fi
    if [ -z "$serial" ] && [ -f /sys/firmware/devicetree/base/serial-number ]; then
        serial=$(cat /sys/firmware/devicetree/base/serial-number | tr -d '\0')
    fi
    if [ -z "$serial" ]; then
        serial=$(cat /sys/class/net/eth0/address 2>/dev/null || cat /sys/class/net/wlan0/address 2>/dev/null || echo "000000000000")
        serial=$(echo "$serial" | tr -d ':' | tr '[:lower:]' '[:upper:]')
    fi
    echo "${serial: -8}"
}

DEVICE_SERIAL=$(get_device_serial)
THING_NAME="TS_${DEVICE_SERIAL^^}"

info "Device Serial: $DEVICE_SERIAL"
info "Thing Name: $THING_NAME"

# ── Step 1: Set unique hostname ──────────────────────────────────────────────

log "Setting hostname to ts-${DEVICE_SERIAL,,}..."
NEW_HOSTNAME="ts-${DEVICE_SERIAL,,}"
CURRENT_HOSTNAME=$(hostname)

if [ "$CURRENT_HOSTNAME" != "$NEW_HOSTNAME" ]; then
    sudo hostnamectl set-hostname "$NEW_HOSTNAME" 2>/dev/null || \
        echo "$NEW_HOSTNAME" | sudo tee /etc/hostname > /dev/null

    # Update /etc/hosts
    if grep -q "$CURRENT_HOSTNAME" /etc/hosts 2>/dev/null; then
        sudo sed -i "s/$CURRENT_HOSTNAME/$NEW_HOSTNAME/g" /etc/hosts
    fi
    if ! grep -q "$NEW_HOSTNAME" /etc/hosts 2>/dev/null; then
        echo "127.0.1.1 $NEW_HOSTNAME" | sudo tee -a /etc/hosts > /dev/null
    fi
    success "Hostname set to $NEW_HOSTNAME"
else
    info "Hostname already set to $NEW_HOSTNAME"
fi

# ── Step 2: Expand filesystem ─────────────────────────────────────────────────

log "Expanding filesystem..."
sudo raspi-config nonint do_expand_rootfs 2>/dev/null || info "Filesystem already expanded"

# ── Step 3: Sync Python dependencies ─────────────────────────────────────────

log "Syncing Python dependencies..."
cd "$SCRIPT_DIR"
if command -v uv &>/dev/null; then
    uv sync 2>&1 | tee -a "$LOG_FILE"
    success "Python dependencies synced"
else
    error "uv not found - install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# ── Step 4: AWS IoT provisioning ─────────────────────────────────────────────

log "AWS IoT certificate provisioning..."
if [ -f "$SCRIPT_DIR/assets/certs/device-config.json" ]; then
    existing_thing=$(jq -r '.thingName' "$SCRIPT_DIR/assets/certs/device-config.json" 2>/dev/null || echo "")
    if [ "$existing_thing" = "$THING_NAME" ]; then
        info "AWS IoT already provisioned for $THING_NAME"
    else
        warning "Existing certs are for $existing_thing, not $THING_NAME"
        warning "Re-provisioning..."
        if [ -f "$SCRIPT_DIR/aws-iot-cert-provisioner.sh" ] && command -v aws &>/dev/null; then
            bash "$SCRIPT_DIR/aws-iot-cert-provisioner.sh" 2>&1 | tee -a "$LOG_FILE" || \
                warning "AWS provisioning failed - will retry on next boot"
        fi
    fi
elif [ -f "$SCRIPT_DIR/aws-iot-cert-provisioner.sh" ] && command -v aws &>/dev/null; then
    if aws sts get-caller-identity &>/dev/null; then
        bash "$SCRIPT_DIR/aws-iot-cert-provisioner.sh" 2>&1 | tee -a "$LOG_FILE" || \
            warning "AWS provisioning failed - will retry on next boot"
    else
        warning "AWS credentials not configured. Skipping cert provisioning."
    fi
else
    warning "AWS provisioning skipped (no script or AWS CLI)"
fi

# ── Step 5: Download media from S3 ───────────────────────────────────────────

log "Downloading media from S3..."
if command -v aws &>/dev/null && aws sts get-caller-identity &>/dev/null; then
    if [ -f "$SCRIPT_DIR/download_s3_videos.sh" ]; then
        bash "$SCRIPT_DIR/download_s3_videos.sh" 2>&1 | tee -a "$LOG_FILE" || \
            warning "Video download failed"
    fi
    if [ -f "$SCRIPT_DIR/download_s3_images.sh" ]; then
        bash "$SCRIPT_DIR/download_s3_images.sh" 2>&1 | tee -a "$LOG_FILE" || \
            warning "Image download failed"
    fi
else
    warning "S3 media download skipped (no AWS credentials)"
fi

# ── Step 6: Enable and start services ─────────────────────────────────────────

log "Enabling systemd services..."
sudo systemctl daemon-reload

# Enable core services
sudo systemctl enable "tsv6-xorg@$CURRENT_USER.service" 2>/dev/null || true
sudo systemctl enable "tsv6@$CURRENT_USER.service" 2>/dev/null || true

success "Systemd services enabled"

# ── Step 7: Write provisioning report ─────────────────────────────────────────

REPORT_FILE="$SCRIPT_DIR/assets/certs/provisioning-report.json"
cat > "$REPORT_FILE" << EOF
{
    "deviceSerial": "$DEVICE_SERIAL",
    "thingName": "$THING_NAME",
    "hostname": "$NEW_HOSTNAME",
    "user": "$CURRENT_USER",
    "provisionedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "ipAddress": "$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'N/A')",
    "macAddress": "$(cat /sys/class/net/wlan0/address 2>/dev/null || echo 'N/A')",
    "piModel": "$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || echo 'N/A')",
    "osVersion": "$(cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'"' -f2 || echo 'N/A')",
    "pythonVersion": "$(python3 --version 2>/dev/null | awk '{print $2}' || echo 'N/A')",
    "uvVersion": "$(uv --version 2>/dev/null || echo 'N/A')",
    "tsv6Version": "$(grep 'version' "$SCRIPT_DIR/pyproject.toml" | head -1 | cut -d'"' -f2 || echo 'N/A')",
    "awsCertsPresent": $([ -f "$SCRIPT_DIR/assets/certs/aws_cert_crt.pem" ] && echo "true" || echo "false"),
    "videosPresent": $([ -d "$SCRIPT_DIR/assets/videos" ] && [ "$(ls -A "$SCRIPT_DIR/assets/videos" 2>/dev/null)" ] && echo "true" || echo "false")
}
EOF

success "Provisioning report: $REPORT_FILE"

# ── Mark complete ─────────────────────────────────────────────────────────────

date -u +%Y-%m-%dT%H:%M:%SZ > "$MARKER_FILE"
success "First-boot provisioning complete!"

echo ""
info "Device: $THING_NAME ($NEW_HOSTNAME)"
info "Log: $LOG_FILE"
info "Report: $REPORT_FILE"
echo ""
warning "Rebooting to apply all changes..."
sleep 3
sudo reboot
