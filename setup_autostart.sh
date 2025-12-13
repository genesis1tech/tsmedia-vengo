#!/bin/bash

################################################################################
# TSV6 Autostart Setup Script
# 
# This script installs and enables the TSV6 systemd service for automatic
# startup on boot.
################################################################################

set -e

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
    error "Please do not run this script as root. Run as a regular user with sudo privileges."
    exit 1
fi

log "🚀 TSV6 Autostart Setup"
echo "=================================="

# Get current directory and user
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$USER"

info "Installation directory: $CURRENT_DIR"
info "User: $CURRENT_USER"

# Check if service file exists
if [ ! -f "$CURRENT_DIR/tsv6.service" ]; then
    error "Service file not found: $CURRENT_DIR/tsv6.service"
    exit 1
fi

# Check if run_production.py exists
if [ ! -f "$CURRENT_DIR/run_production.py" ]; then
    error "Production script not found: $CURRENT_DIR/run_production.py"
    exit 1
fi

# Check if UV is installed
if ! command -v uv &> /dev/null; then
    error "UV package manager not found. Please install UV first."
    error "Run: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Setup serial access for STServo bus servo adapter
log "Setting up serial device access for servo..."
log "Adding user to dialout group for serial access..."
sudo usermod -a -G dialout $CURRENT_USER
success "User added to dialout group for servo serial access"

# Setup input device access for barcode scanner
log "Setting up input device access for barcode scanner..."

# Add user to input group
log "Adding user to input group..."
sudo usermod -a -G input $CURRENT_USER
success "User added to input group"

# Install udev rules for barcode scanner
UDEV_RULES_FILE="$CURRENT_DIR/scripts/udev/99-tsv6-barcode.rules"
if [ -f "$UDEV_RULES_FILE" ]; then
    log "Installing udev rules for barcode scanner..."
    sudo cp "$UDEV_RULES_FILE" /etc/udev/rules.d/
    sudo chmod 644 /etc/udev/rules.d/99-tsv6-barcode.rules
    sudo udevadm control --reload-rules
    success "Udev rules installed for barcode scanner access"
else
    warning "Udev rules file not found: $UDEV_RULES_FILE"
fi

# Create a customized service file with the current user and directory
log "Creating customized service file..."
SERVICE_FILE="/tmp/tsv6@.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=TSV6 Raspberry Pi Video Player
After=network-online.target time-sync.target tsv6-xorg@%i.service
Wants=network-online.target tsv6-xorg@%i.service

[Service]
Type=simple
User=%i
WorkingDirectory=$CURRENT_DIR
Environment="PATH=/home/%i/.local/bin:/home/%i/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
Environment="DISPLAY=:0"
Environment="XAUTHORITY=/home/%i/.Xauthority"
ExecStartPre=/bin/sleep 5
ExecStart=/home/%i/.local/bin/uv run python run_production.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tsv6

[Install]
WantedBy=multi-user.target
EOF

# Install service file
log "Installing service file..."
sudo cp "$SERVICE_FILE" "/etc/systemd/system/tsv6@.service"
sudo chmod 644 "/etc/systemd/system/tsv6@.service"
success "Service file installed to /etc/systemd/system/tsv6@.service"

# Reload systemd
log "Reloading systemd daemon..."
sudo systemctl daemon-reload
success "Systemd daemon reloaded"

# Enable service for current user
log "Enabling TSV6 service for user: $CURRENT_USER..."
sudo systemctl enable "tsv6@$CURRENT_USER.service"
success "TSV6 service enabled for $CURRENT_USER"

# Ask if user wants to start the service now
echo ""
read -p "Do you want to start the TSV6 service now? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    log "Starting TSV6 service..."
    sudo systemctl start "tsv6@$CURRENT_USER.service"
    sleep 3
    
    # Check service status
    if sudo systemctl is-active --quiet "tsv6@$CURRENT_USER.service"; then
        success "TSV6 service is running!"
        info "View logs with: sudo journalctl -u tsv6@$CURRENT_USER.service -f"
    else
        warning "Service failed to start. Check logs with:"
        warning "  sudo journalctl -u tsv6@$CURRENT_USER.service -n 50"
    fi
else
    info "Service will start automatically on next boot"
fi

# Display service management commands
echo ""
echo "=================================="
echo "📋 Service Management Commands"
echo "=================================="
echo "Check status:  sudo systemctl status tsv6@$CURRENT_USER.service"
echo "Start service: sudo systemctl start tsv6@$CURRENT_USER.service"
echo "Stop service:  sudo systemctl stop tsv6@$CURRENT_USER.service"
echo "Restart:       sudo systemctl restart tsv6@$CURRENT_USER.service"
echo "View logs:     sudo journalctl -u tsv6@$CURRENT_USER.service -f"
echo "Disable:       sudo systemctl disable tsv6@$CURRENT_USER.service"
echo ""

success "🎉 TSV6 autostart setup complete!"
info "TSV6 will automatically start on boot"

# Clean up temp file
rm -f "$SERVICE_FILE"

exit 0