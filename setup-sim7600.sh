#!/bin/bash
# =============================================================================
# SIM7600NA-H 4G LTE HAT Setup Script for TSV6
#
# This script configures the Waveshare SIM7600NA-H 4G LTE HAT on Raspberry Pi
# for use with Hologram.io as the service provider.
#
# Prerequisites:
# - Raspberry Pi 4B or 5 with Raspberry Pi OS (Bookworm)
# - SIM7600NA-H HAT installed
# - Hologram SIM card activated via Hologram Dashboard
#
# Reference: https://www.waveshare.com/wiki/SIM7600NA-H_4G_HAT
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "Please run as root (sudo $0)"
        exit 1
    fi
}

# Detect Raspberry Pi model
detect_pi_model() {
    if [ -f /proc/device-tree/model ]; then
        PI_MODEL=$(cat /proc/device-tree/model)
        log_info "Detected: $PI_MODEL"

        if echo "$PI_MODEL" | grep -q "Raspberry Pi 5"; then
            PI_VERSION=5
        elif echo "$PI_MODEL" | grep -q "Raspberry Pi 4"; then
            PI_VERSION=4
        else
            PI_VERSION=0
            log_warn "Unknown Raspberry Pi model, some features may not work"
        fi
    else
        PI_VERSION=0
        log_warn "Could not detect Raspberry Pi model"
    fi
}

# Install required packages
install_packages() {
    log_info "Installing required packages..."

    apt-get update
    apt-get install -y \
        ppp \
        usb-modeswitch \
        minicom \
        network-manager \
        modemmanager \
        mobile-broadband-provider-info

    log_success "System packages installed"
    log_info "Note: pyserial is managed via uv (pyproject.toml)"
}

# Configure serial port
configure_serial() {
    log_info "Configuring serial port..."

    # Disable serial console (if enabled)
    if grep -q "console=serial0" /boot/cmdline.txt 2>/dev/null || grep -q "console=ttyAMA0" /boot/cmdline.txt 2>/dev/null; then
        log_info "Disabling serial console..."
        sed -i 's/console=serial0,[0-9]* //g' /boot/cmdline.txt
        sed -i 's/console=ttyAMA0,[0-9]* //g' /boot/cmdline.txt
    fi

    # Enable UART in config.txt
    if ! grep -q "^enable_uart=1" /boot/config.txt 2>/dev/null && ! grep -q "^enable_uart=1" /boot/firmware/config.txt 2>/dev/null; then
        log_info "Enabling UART..."
        # Try both locations (older vs newer Pi OS)
        if [ -f /boot/firmware/config.txt ]; then
            echo "enable_uart=1" >> /boot/firmware/config.txt
        else
            echo "enable_uart=1" >> /boot/config.txt
        fi
    fi

    log_success "Serial port configured"
}

# Create udev rules for consistent device naming
create_udev_rules() {
    log_info "Creating udev rules for SIM7600..."

    cat > /etc/udev/rules.d/99-sim7600.rules << 'EOF'
# Waveshare SIM7600NA-H 4G HAT udev rules
# Creates consistent symlinks for modem serial ports

# SIM7600 USB serial interfaces (Vendor ID: 1e0e, Product ID: 9011 for RNDIS mode)
# ttyUSB0 = Diagnostic port
# ttyUSB1 = GPS NMEA output
# ttyUSB2 = AT command port (main control)
# ttyUSB3 = Modem port (PPP)

# AT command port - main control interface
SUBSYSTEM=="tty", ATTRS{idVendor}=="1e0e", ATTRS{idProduct}=="9011", ENV{ID_USB_INTERFACE_NUM}=="02", SYMLINK+="tsv6-lte", MODE="0666"

# Diagnostic port
SUBSYSTEM=="tty", ATTRS{idVendor}=="1e0e", ATTRS{idProduct}=="9011", ENV{ID_USB_INTERFACE_NUM}=="00", SYMLINK+="tsv6-lte-diag", MODE="0666"

# GPS NMEA port
SUBSYSTEM=="tty", ATTRS{idVendor}=="1e0e", ATTRS{idProduct}=="9011", ENV{ID_USB_INTERFACE_NUM}=="01", SYMLINK+="tsv6-lte-gps", MODE="0666"

# Modem/PPP port
SUBSYSTEM=="tty", ATTRS{idVendor}=="1e0e", ATTRS{idProduct}=="9011", ENV{ID_USB_INTERFACE_NUM}=="03", SYMLINK+="tsv6-lte-modem", MODE="0666"

# RNDIS USB network interface - set consistent name
SUBSYSTEM=="net", ATTRS{idVendor}=="1e0e", ATTRS{idProduct}=="9011", NAME="usb0"

# NDIS mode (alternative, Product ID: 9001)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1e0e", ATTRS{idProduct}=="9001", ENV{ID_USB_INTERFACE_NUM}=="02", SYMLINK+="tsv6-lte", MODE="0666"
EOF

    # Reload udev rules
    udevadm control --reload-rules
    udevadm trigger

    log_success "udev rules created"
}

# Setup GPIO power control for modem
setup_gpio_power() {
    log_info "Setting up GPIO power control..."

    # Create systemd service for GPIO power control
    cat > /etc/systemd/system/tsv6-lte-power.service << 'EOF'
[Unit]
Description=TSV6 LTE Modem Power Control
Before=network-pre.target ModemManager.service
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
# Power on via GPIO D6 (BCM 6) - SIM7600 HAT power control pin
ExecStart=/usr/bin/pinctrl set 6 op dh
ExecStop=/usr/bin/pinctrl set 6 op dl

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable tsv6-lte-power.service

    log_success "GPIO power control configured"
}

# Create NetworkManager connection for Hologram
setup_network_manager() {
    log_info "Setting up NetworkManager connection for Hologram..."

    # Check if NetworkManager is running
    if ! systemctl is-active --quiet NetworkManager; then
        log_warn "NetworkManager not running, skipping connection setup"
        return
    fi

    # Remove existing connection if present
    nmcli connection delete "hologram-lte" 2>/dev/null || true

    # Create new connection with Hologram settings
    # Route metric 100 = LTE primary (lower is higher priority)
    nmcli connection add \
        type gsm \
        ifname '*' \
        con-name "hologram-lte" \
        gsm.apn "hologram" \
        gsm.auto-config yes \
        connection.autoconnect no \
        connection.autoconnect-priority -100 \
        ipv4.method auto \
        ipv4.route-metric 100 \
        ipv4.never-default no \
        ipv6.method ignore || {
            log_warn "Failed to create NetworkManager connection"
            return
        }

    log_success "NetworkManager connection 'hologram-lte' created"
}

# Add user to dialout group
setup_user_permissions() {
    log_info "Setting up user permissions..."

    # Get the user who invoked sudo
    ACTUAL_USER=${SUDO_USER:-$(whoami)}

    if [ "$ACTUAL_USER" != "root" ]; then
        usermod -a -G dialout "$ACTUAL_USER"
        log_success "User '$ACTUAL_USER' added to dialout group"
    fi
}

# Create test script
create_test_script() {
    log_info "Creating test script..."

    cat > /home/${SUDO_USER:-pi}/test_sim7600.sh << 'EOF'
#!/bin/bash
# Test SIM7600 modem connection

echo "=== SIM7600 Test Script ==="
echo ""

# Check for device
echo "Checking for modem device..."
if [ -e /dev/tsv6-lte ]; then
    echo "Found: /dev/tsv6-lte"
else
    echo "NOT FOUND: /dev/tsv6-lte"
    echo "Checking USB devices..."
    ls -la /dev/ttyUSB* 2>/dev/null || echo "No ttyUSB devices found"
fi
echo ""

# Test AT commands if device exists
if [ -e /dev/tsv6-lte ]; then
    echo "Testing AT commands..."

    # Send AT command and read response
    exec 3<>/dev/tsv6-lte
    stty -F /dev/tsv6-lte 115200 raw -echo

    # Basic AT test
    echo -e "AT\r" >&3
    sleep 1
    read -t 2 response <&3 || true
    echo "AT Response: $response"

    # SIM status
    echo -e "AT+CPIN?\r" >&3
    sleep 1
    read -t 2 response <&3 || true
    echo "SIM Status: $response"

    # Signal quality
    echo -e "AT+CSQ\r" >&3
    sleep 1
    read -t 2 response <&3 || true
    echo "Signal Quality: $response"

    # Network registration
    echo -e "AT+CGREG?\r" >&3
    sleep 1
    read -t 2 response <&3 || true
    echo "Network Registration: $response"

    exec 3>&-
fi

echo ""
echo "=== Test Complete ==="
EOF

    chmod +x /home/${SUDO_USER:-pi}/test_sim7600.sh
    chown ${SUDO_USER:-pi}:${SUDO_USER:-pi} /home/${SUDO_USER:-pi}/test_sim7600.sh

    log_success "Test script created at ~/test_sim7600.sh"
}

# Update tsv6.service with LTE environment variables
update_tsv6_service() {
    log_info "Updating tsv6.service with LTE configuration..."

    SERVICE_FILE="/home/g1tech/tsrpi5/tsv6.service"

    if [ -f "$SERVICE_FILE" ]; then
        # Check if LTE vars already present
        if ! grep -q "TSV6_LTE_ENABLED" "$SERVICE_FILE"; then
            # Add LTE environment variables before the [Install] section
            sed -i '/^\[Install\]/i \
# LTE Configuration (Hologram.io)\
Environment="TSV6_LTE_ENABLED=true"\
Environment="TSV6_LTE_APN=hologram"\
Environment="TSV6_CONNECTIVITY_MODE=lte_primary_wifi_backup"\
' "$SERVICE_FILE"
            log_success "LTE environment variables added to tsv6.service"
        else
            log_info "LTE environment variables already present in tsv6.service"
        fi
    else
        log_warn "tsv6.service not found, skipping service update"
    fi
}

# Print summary
print_summary() {
    echo ""
    echo "=============================================="
    echo "  SIM7600 Setup Complete"
    echo "=============================================="
    echo ""
    echo "What was configured:"
    echo "  - Serial port (UART enabled)"
    echo "  - udev rules (/dev/tsv6-lte symlink)"
    echo "  - GPIO power control service"
    echo "  - NetworkManager connection 'hologram-lte'"
    echo "  - User permissions (dialout group)"
    echo ""
    echo "Next steps:"
    echo "  1. Insert activated Hologram SIM card"
    echo "  2. Reboot the Raspberry Pi"
    echo "  3. Run: ~/test_sim7600.sh"
    echo "  4. Verify device: ls -la /dev/tsv6-lte*"
    echo ""
    echo "To enable LTE in TSV6:"
    echo "  Set environment variable: TSV6_LTE_ENABLED=true"
    echo "  Or update tsv6.service (already done if service exists)"
    echo ""
    echo "Hologram Dashboard:"
    echo "  https://dashboard.hologram.io"
    echo ""
    echo "IMPORTANT: A reboot is required for changes to take effect!"
    echo ""
}

# Main execution
main() {
    echo ""
    echo "=============================================="
    echo "  TSV6 SIM7600NA-H 4G LTE HAT Setup"
    echo "  Service Provider: Hologram.io"
    echo "=============================================="
    echo ""

    check_root
    detect_pi_model
    install_packages
    configure_serial
    create_udev_rules
    setup_gpio_power
    setup_network_manager
    setup_user_permissions
    create_test_script
    update_tsv6_service
    print_summary
}

# Run main function
main "$@"
