#!/bin/bash
#
# WiFi Provisioning Setup Script for TSV6
# ========================================
# Installs dependencies and configures systemd services for
# WiFi provisioning on first boot.
#
# Usage: sudo ./setup-wifi-provisioning.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Determine the user (handle sudo case)
if [ -n "$SUDO_USER" ]; then
    TARGET_USER="$SUDO_USER"
else
    TARGET_USER="$(whoami)"
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}TSV6 WiFi Provisioning Setup${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root (use sudo)${NC}"
    exit 1
fi

echo -e "${YELLOW}Target user: ${TARGET_USER}${NC}"
echo -e "${YELLOW}Script directory: ${SCRIPT_DIR}${NC}"
echo ""

# Step 1: Install dependencies
echo -e "${GREEN}[1/5] Installing dependencies...${NC}"
apt-get update -qq
apt-get install -y -qq hostapd dnsmasq

# Step 2: Disable hostapd and dnsmasq services by default
# (We control them programmatically during provisioning)
echo -e "${GREEN}[2/5] Configuring hostapd and dnsmasq...${NC}"
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true
systemctl mask hostapd 2>/dev/null || true
systemctl mask dnsmasq 2>/dev/null || true
echo "  - hostapd and dnsmasq masked (will be started manually during provisioning)"

# Step 3: Install provisioning systemd service
echo -e "${GREEN}[3/5] Installing systemd service...${NC}"
SERVICE_FILE="${SCRIPT_DIR}/tsv6-wifi-provisioning.service"

if [ ! -f "$SERVICE_FILE" ]; then
    echo -e "${RED}Error: Service file not found: ${SERVICE_FILE}${NC}"
    exit 1
fi

# Replace %i with actual username in service file
sed "s/%i/${TARGET_USER}/g" "$SERVICE_FILE" > /etc/systemd/system/tsv6-wifi-provisioning.service
echo "  - Installed tsv6-wifi-provisioning.service"

# Step 4: Update main TSV6 service if needed
echo -e "${GREEN}[4/5] Updating main TSV6 service...${NC}"
MAIN_SERVICE="${SCRIPT_DIR}/tsv6.service"

if [ -f "$MAIN_SERVICE" ]; then
    sed "s/%i/${TARGET_USER}/g" "$MAIN_SERVICE" > /etc/systemd/system/tsv6.service
    echo "  - Updated tsv6.service with provisioning dependency"
fi

# Step 5: Enable and reload services
echo -e "${GREEN}[5/5] Enabling services...${NC}"
systemctl daemon-reload
systemctl enable tsv6-wifi-provisioning.service
echo "  - tsv6-wifi-provisioning.service enabled"

# Summary
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "WiFi provisioning is now configured."
echo ""
echo "On next boot:"
echo "  1. If WiFi is not configured, device will create hotspot:"
echo "     - SSID: TS_<device_id>"
echo "     - Password: recycleit"
echo "  2. Connect to hotspot and open browser"
echo "  3. Enter your WiFi credentials"
echo "  4. Device will connect and start main application"
echo ""
echo "To test provisioning now (WARNING: will reset WiFi):"
echo "  sudo systemctl start tsv6-wifi-provisioning.service"
echo ""
echo "To check status:"
echo "  sudo systemctl status tsv6-wifi-provisioning.service"
echo "  sudo journalctl -u tsv6-wifi-provision -f"
echo ""
