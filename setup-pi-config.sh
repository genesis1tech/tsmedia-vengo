#!/bin/bash
################################################################################
# TSV6 Raspberry Pi Configuration Script
#
# Configures Raspberry Pi 5 hardware settings for TSV6:
#   - raspi-config settings (I2C, SPI, SSH, boot behavior)
#   - config.txt for Waveshare 7" DSI display
#   - GPU memory allocation (256MB for Pi 5)
#   - PCIe Gen 3 for faster I/O
#   - System boot target (multi-user.target)
#
# Target: Raspberry Pi 5 with Waveshare 7" DSI Display
# Run after: setup-dependencies.sh
################################################################################

set -e

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    error "Please do not run this script as root. Run as a regular user with sudo privileges."
    exit 1
fi

# Check if running on Raspberry Pi
if [ ! -f /proc/cpuinfo ] || ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    warning "This script is designed for Raspberry Pi. Some settings may not apply."
fi

log "TSV6 Raspberry Pi Configuration"
echo "=================================="

# ============================================================================
# raspi-config Settings
# ============================================================================
log "Configuring raspi-config settings..."

# Enable I2C (for future sensor support)
info "Enabling I2C interface..."
sudo raspi-config nonint do_i2c 0 2>/dev/null || warning "I2C config not available"

# Enable SPI (for future sensor support)
info "Enabling SPI interface..."
sudo raspi-config nonint do_spi 0 2>/dev/null || warning "SPI config not available"

# Enable SSH
info "Enabling SSH..."
sudo raspi-config nonint do_ssh 0 2>/dev/null || warning "SSH config not available"

# Expand filesystem
info "Expanding filesystem..."
sudo raspi-config nonint do_expand_rootfs 2>/dev/null || warning "Filesystem expansion not available"

# Configure boot behaviour for console autologin (NOT desktop)
info "Configuring console autologin..."
sudo raspi-config nonint do_boot_behaviour B2 2>/dev/null || warning "Boot behaviour config not available"

# Disable boot splash for faster boot
info "Disabling boot splash..."
sudo raspi-config nonint do_boot_splash 1 2>/dev/null || warning "Boot splash config not available"

success "raspi-config settings applied"

# ============================================================================
# Managed boot config
# ============================================================================
log "Installing managed Raspberry Pi boot config..."
sudo bash "$SCRIPT_DIR/scripts/install-boot-config.sh"
success "Managed boot config installed"

# ============================================================================
# Network Wait Configuration
# ============================================================================
log "Configuring network settings..."

# Disable network wait at boot
if [ -f /etc/systemd/system/dhcpcd.service.d/wait.conf ]; then
    sudo rm /etc/systemd/system/dhcpcd.service.d/wait.conf
fi
sudo systemctl disable systemd-networkd-wait-online.service 2>/dev/null || true

success "Network wait disabled"

# ============================================================================
# System Boot Target
# ============================================================================
log "Setting system boot target..."

# Set system default to multi-user.target (console, not graphical)
sudo systemctl set-default multi-user.target
info "System will boot to console (multi-user.target)"

success "Boot target configured"

# ============================================================================
# Validation
# ============================================================================
log "Validating configuration..."

# Check GPU memory
if command -v vcgencmd &> /dev/null; then
    GPU_MEM=$(vcgencmd get_mem gpu 2>/dev/null | cut -d'=' -f2 | tr -d 'M')
    if [ -n "$GPU_MEM" ]; then
        info "Current GPU memory: ${GPU_MEM}MB (will be 256MB after reboot)"
    fi
fi

# Check display devices
if [ -e /dev/dri/card0 ]; then
    info "DRM device detected: /dev/dri/card0"
else
    warning "No DRM device found - will appear after reboot"
fi

if [ -e /dev/fb0 ]; then
    info "Framebuffer device detected: /dev/fb0"
fi

# Check system target
DEFAULT_TARGET=$(systemctl get-default)
info "System default target: $DEFAULT_TARGET"

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "=================================="
log "Configuration Summary"
echo "=================================="
echo ""
info "Applied settings:"
echo "  - I2C/SPI interfaces enabled"
echo "  - SSH enabled"
echo "  - Filesystem expanded"
echo "  - Console autologin configured"
echo "  - Boot splash disabled"
echo "  - Waveshare 7\" DSI display configured"
echo "  - HDMI output enabled for external portable monitor (1080p60)"
echo "  - GPU memory: 256MB"
echo "  - PCIe Gen 3 enabled"
echo "  - CMA: 256M"
echo "  - I2C bus 2 enabled (dtoverlay=i2c2-pi5 for recycling sensor)"
echo "  - Boot target: multi-user.target"
echo ""
warning "REBOOT REQUIRED for changes to take effect!"
echo ""
info "Next steps:"
echo "  1. Run ./setup-services.sh to install systemd services"
echo "  2. Run ./setup-security.sh for security hardening (optional)"
echo "  3. Reboot: sudo reboot"
echo ""

exit 0
