#!/bin/bash
################################################################################
# TSV6 System Dependencies Setup Script
#
# This script installs all required system packages for the TSV6 video player
# on Raspberry Pi OS 64-bit Lite (headless).
#
# Dependencies installed:
#   - X11/Xorg (display server for headless video playback)
#   - VLC with plugins (video playback engine)
#   - Python build dependencies (for native Python packages)
#   - System libraries for pygame, dbus, etc.
#
# After running this script, use 'uv sync' to install Python dependencies.
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

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$USER"

log "TSV6 System Dependencies Setup"
echo "=================================="
info "Installation directory: $SCRIPT_DIR"
info "User: $CURRENT_USER"
echo ""

# Update package lists
log "Updating package lists..."
sudo apt-get update

# Install X11/Xorg for display (required for headless video playback)
log "Installing X11/Xorg display server..."
sudo apt-get install -y \
    xorg \
    xinit \
    x11-xserver-utils

success "X11/Xorg installed"

# Install VLC with all plugins (vlc-bin alone is not enough for python-vlc)
log "Installing VLC media player with plugins..."
sudo apt-get install -y \
    vlc \
    vlc-plugin-base \
    vlc-plugin-video-output

success "VLC installed with plugins"

# Install build tools and Python build dependencies
log "Installing build tools and Python dependencies..."
sudo apt-get install -y \
    build-essential \
    python3-dev \
    python3-venv \
    python3-tk \
    libdbus-1-dev \
    libdbus-glib-1-dev \
    pkg-config

success "Build tools and Python dependencies installed"

# Install AWS IoT SDK build dependencies
log "Installing AWS IoT SDK build dependencies..."
sudo apt-get install -y \
    cmake \
    libssl-dev

success "AWS IoT SDK build dependencies installed"

# Install SDL libraries for pygame
log "Installing SDL libraries for pygame..."
sudo apt-get install -y \
    libsdl2-dev \
    libsdl2-image-dev \
    libsdl2-mixer-dev \
    libsdl2-ttf-dev \
    libportmidi-dev \
    libfreetype6-dev

success "SDL libraries installed"

# Install image processing libraries for Pillow
log "Installing image processing libraries..."
sudo apt-get install -y \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    libopenjp2-7

success "Image processing libraries installed"

# Install GPIO and I2C libraries for hardware support
log "Installing GPIO and I2C libraries..."
sudo apt-get install -y \
    python3-rpi.gpio \
    i2c-tools \
    python3-smbus \
    libgpiod-dev

success "GPIO and I2C libraries installed"

# Install networking tools (NetworkManager, WiFi provisioning, diagnostics)
log "Installing networking packages..."
sudo apt-get install -y \
    network-manager \
    hostapd \
    dnsmasq \
    iw \
    wireless-tools \
    iputils-ping

success "Networking packages installed"

# Ensure hostapd and dnsmasq don't auto-start (only used on-demand by provisioner)
sudo systemctl disable hostapd 2>/dev/null || true
sudo systemctl stop hostapd 2>/dev/null || true
sudo systemctl disable dnsmasq 2>/dev/null || true
sudo systemctl stop dnsmasq 2>/dev/null || true
info "hostapd/dnsmasq disabled (started on-demand by WiFi provisioner)"

# Install NFC tools (libnfc for nfc-emulate-forum-tag4)
log "Installing NFC libraries..."
sudo apt-get install -y \
    libnfc-bin \
    libnfc-dev \
    || warning "NFC packages not available in repo (NFC emulation will be disabled)"

success "NFC libraries installed"

# Install fonts for tkinter/pygame text rendering
log "Installing fonts..."
sudo apt-get install -y \
    fonts-dejavu-core

success "Fonts installed"

# Install additional system utilities
log "Installing system utilities..."
sudo apt-get install -y \
    bc \
    curl \
    psmisc \
    htop \
    jq \
    git \
    awscli

success "System utilities installed"

# Configure git credential caching (avoids repeated GitHub token prompts)
log "Configuring git credential store..."
git config --global credential.helper store
success "Git credential store enabled"

# Configure Xwrapper to allow X server from systemd services
log "Configuring X server permissions..."
XWRAPPER_CONFIG="/etc/X11/Xwrapper.config"

if [ -f "$XWRAPPER_CONFIG" ]; then
    if grep -q "allowed_users=console" "$XWRAPPER_CONFIG"; then
        sudo sed -i 's/allowed_users=console/allowed_users=anybody/' "$XWRAPPER_CONFIG"
        success "X server configured to allow systemd services"
    elif grep -q "allowed_users=anybody" "$XWRAPPER_CONFIG"; then
        info "X server already configured for systemd services"
    else
        echo "allowed_users=anybody" | sudo tee -a "$XWRAPPER_CONFIG" > /dev/null
        success "X server permission added"
    fi
else
    warning "Xwrapper.config not found - X server may need manual configuration"
fi

# Check if UV is installed
log "Checking for UV package manager..."
if command -v uv &> /dev/null; then
    success "UV is installed: $(uv --version)"
else
    warning "UV not found. Installing UV..."
    curl -LsSf --connect-timeout 15 --max-time 120 https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    success "UV installed"
fi

# Sync Python dependencies with UV
log "Syncing Python dependencies with UV..."
cd "$SCRIPT_DIR"
if [ -f "pyproject.toml" ]; then
    uv sync
    success "Python dependencies synced"
else
    error "pyproject.toml not found in $SCRIPT_DIR"
    exit 1
fi

# Create required directories
log "Creating required directories..."
mkdir -p "$SCRIPT_DIR/logs"
mkdir -p "$SCRIPT_DIR/data"
mkdir -p "$SCRIPT_DIR/assets/certs"
sudo mkdir -p /var/log/tsv6
sudo mkdir -p /var/lib/tsv6
sudo chown "$CURRENT_USER:$CURRENT_USER" /var/log/tsv6 /var/lib/tsv6

success "Directories created"

# Display summary
echo ""
echo "=================================="
log "Installation Summary"
echo "=================================="
echo ""
info "System packages installed:"
echo "  - X11/Xorg (display server)"
echo "  - VLC with plugins (video playback)"
echo "  - Build tools and Python dependencies"
echo "  - AWS IoT SDK build dependencies"
echo "  - SDL libraries (pygame)"
echo "  - Image processing libraries (Pillow)"
echo "  - GPIO and I2C libraries (rpi-gpio, adafruit-blinka support)"
echo "  - Networking (NetworkManager, hostapd, dnsmasq, iw, wireless-tools)"
echo "  - NFC (libnfc-bin for nfc-emulate-forum-tag4)"
echo "  - Fonts (fonts-dejavu-core for tkinter/pygame)"
echo "  - System utilities (git, awscli, jq, htop, curl)"
echo ""
info "Configuration applied:"
echo "  - X server allowed for systemd services"
echo ""
info "Python dependencies synced with UV"
echo ""

# Verify installations
log "Verifying installations..."
echo ""

# Check VLC
if python3 -c "import vlc; i=vlc.Instance(); print('VLC:', 'OK' if i else 'FAILED')" 2>/dev/null; then
    success "VLC Python bindings working"
else
    # Try with venv python
    if "$SCRIPT_DIR/.venv/bin/python3" -c "import vlc; i=vlc.Instance(); print('VLC:', 'OK' if i else 'FAILED')" 2>/dev/null; then
        success "VLC Python bindings working (venv)"
    else
        warning "VLC Python bindings may need display - will work when X is running"
    fi
fi

# Check X11
if [ -f "/etc/X11/Xwrapper.config" ]; then
    success "X11 configuration present"
fi

echo ""
echo "=================================="
success "TSV6 dependencies setup complete!"
echo "=================================="
echo ""
info "Next steps:"
echo "  1. Configure Raspberry Pi:   ./setup-pi-config.sh"
echo "  2. Install systemd services: ./setup-services.sh"
echo "  3. Security hardening (opt): ./setup-security.sh"
echo "  4. Provision AWS IoT certs:  ./aws-iot-cert-provisioner.sh"
echo "  5. Reboot to apply changes:  sudo reboot"
echo ""
info "After reboot, TSV6 will start automatically."
echo ""

exit 0
