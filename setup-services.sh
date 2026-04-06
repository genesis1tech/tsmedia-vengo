#!/bin/bash
################################################################################
# TSV6 Systemd Services Setup Script
#
# Installs and configures systemd services for TSV6:
#   - tsv6-xorg@.service (X11 server for headless display)
#   - tsv6.service (main application)
#   - User group memberships for hardware access
#   - Runtime directories
#
# Run after: setup-dependencies.sh, setup-pi-config.sh
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

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    error "Please do not run this script as root. Run as a regular user with sudo privileges."
    exit 1
fi

# Get script directory and user
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$USER"

log "TSV6 Systemd Services Setup"
echo "=================================="
info "Installation directory: $SCRIPT_DIR"
info "User: $CURRENT_USER"
echo ""

# ============================================================================
# User Group Memberships
# ============================================================================
log "Configuring user group memberships..."

# dialout - Serial port access for STServo USB adapter
if getent group dialout > /dev/null 2>&1; then
    sudo usermod -a -G dialout "$CURRENT_USER"
    info "Added $CURRENT_USER to dialout group (STServo serial access)"
else
    warning "dialout group not found"
fi

# input - Input device access for barcode scanner
if getent group input > /dev/null 2>&1; then
    sudo usermod -a -G input "$CURRENT_USER"
    info "Added $CURRENT_USER to input group (barcode scanner access)"
else
    warning "input group not found"
fi

# i2c - I2C access for future sensors
if getent group i2c > /dev/null 2>&1; then
    sudo usermod -a -G i2c "$CURRENT_USER"
    info "Added $CURRENT_USER to i2c group (sensor access)"
else
    warning "i2c group not found"
fi

# spi - SPI access for future sensors
if getent group spi > /dev/null 2>&1; then
    sudo usermod -a -G spi "$CURRENT_USER"
    info "Added $CURRENT_USER to spi group (sensor access)"
else
    warning "spi group not found"
fi

# video - Video device access
if getent group video > /dev/null 2>&1; then
    sudo usermod -a -G video "$CURRENT_USER"
    info "Added $CURRENT_USER to video group"
fi

success "User group memberships configured"

# ============================================================================
# Runtime Directories
# ============================================================================
log "Creating runtime directories..."

mkdir -p "$SCRIPT_DIR/logs"
mkdir -p "$SCRIPT_DIR/data"
mkdir -p "$SCRIPT_DIR/data/cache"
mkdir -p "$SCRIPT_DIR/data/temp"
mkdir -p "$SCRIPT_DIR/data/state"
mkdir -p "$SCRIPT_DIR/assets/certs"

# System directories
sudo mkdir -p /var/log/tsv6
sudo mkdir -p /var/lib/tsv6
sudo chown "$CURRENT_USER:$CURRENT_USER" /var/log/tsv6 /var/lib/tsv6

success "Runtime directories created"

# ============================================================================
# Install tsv6-xorg@.service
# ============================================================================
log "Installing tsv6-xorg@.service..."

if [ -f "$SCRIPT_DIR/tsv6-xorg@.service" ]; then
    sudo cp "$SCRIPT_DIR/tsv6-xorg@.service" /etc/systemd/system/
    sudo chmod 644 /etc/systemd/system/tsv6-xorg@.service
    info "Copied tsv6-xorg@.service from project"
else
    error "tsv6-xorg@.service not found in $SCRIPT_DIR"
    exit 1
fi

# Enable for current user
sudo systemctl daemon-reload
sudo systemctl enable "tsv6-xorg@$CURRENT_USER.service"

success "tsv6-xorg@$CURRENT_USER.service installed and enabled"

# ============================================================================
# Install tsv6.service (convert to template if needed)
# ============================================================================
log "Installing tsv6.service..."

if [ -f "$SCRIPT_DIR/tsv6.service" ]; then
    # tsv6.service already uses %i template variables — install directly as tsv6@.service
    sudo cp "$SCRIPT_DIR/tsv6.service" /etc/systemd/system/tsv6@.service
    sudo chmod 644 /etc/systemd/system/tsv6@.service
    info "Installed tsv6@.service template"
else
    error "tsv6.service not found in $SCRIPT_DIR"
    exit 1
fi

# Enable for current user
sudo systemctl daemon-reload
sudo systemctl enable "tsv6@$CURRENT_USER.service"

success "tsv6@$CURRENT_USER.service installed and enabled"

# ============================================================================
# Install Optional Services
# ============================================================================
log "Installing optional services..."

# Video watchdog
if [ -f "$SCRIPT_DIR/video-watchdog.service" ]; then
    sudo cp "$SCRIPT_DIR/video-watchdog.service" /etc/systemd/system/
    sudo chmod 644 /etc/systemd/system/video-watchdog.service
    info "Installed video-watchdog.service"
fi

# Obstruction handler
if [ -f "$SCRIPT_DIR/tsv6-obstruction-handler.service" ]; then
    sudo cp "$SCRIPT_DIR/tsv6-obstruction-handler.service" /etc/systemd/system/
    sudo chmod 644 /etc/systemd/system/tsv6-obstruction-handler.service
    info "Installed tsv6-obstruction-handler.service"
fi

# WiFi provisioning
if [ -f "$SCRIPT_DIR/tsv6-wifi-provisioning.service" ]; then
    sudo cp "$SCRIPT_DIR/tsv6-wifi-provisioning.service" /etc/systemd/system/
    sudo chmod 644 /etc/systemd/system/tsv6-wifi-provisioning.service
    info "Installed tsv6-wifi-provisioning.service"
fi

# Sleep service
if [ -f "$SCRIPT_DIR/sleep.service" ]; then
    sudo cp "$SCRIPT_DIR/sleep.service" /etc/systemd/system/
    sudo chmod 644 /etc/systemd/system/sleep.service
    info "Installed sleep.service"
fi

# First-boot provisioning service (for golden image deployment)
if [ -f "$SCRIPT_DIR/tsv6-first-boot.service" ]; then
    sudo cp "$SCRIPT_DIR/tsv6-first-boot.service" /etc/systemd/system/tsv6-first-boot@.service
    sudo chmod 644 /etc/systemd/system/tsv6-first-boot@.service
    sudo systemctl enable "tsv6-first-boot@$CURRENT_USER.service" 2>/dev/null || true
    info "Installed tsv6-first-boot@.service (golden image first-boot provisioning)"
fi

sudo systemctl daemon-reload

success "Optional services installed"

# ============================================================================
# Create Diagnostic Scripts
# ============================================================================
log "Creating diagnostic scripts..."

# Display diagnostics script
cat > "$HOME/display_diagnostics.sh" << 'EOL'
#!/bin/bash
# TSV6 Display diagnostics script
echo "=== TSV6 Display Diagnostics ==="
echo "Date: $(date)"
echo ""
echo "GPU Memory: $(vcgencmd get_mem gpu 2>/dev/null || echo 'N/A')"
echo "Framebuffer devices: $(ls -la /dev/fb* 2>/dev/null || echo 'None found')"
echo "DRM devices: $(ls -la /dev/dri/* 2>/dev/null || echo 'None found')"
echo ""
echo "Config.txt DSI settings:"
grep -E "(dtoverlay.*dsi|framebuffer|gpu_mem)" /boot/firmware/config.txt 2>/dev/null || echo "No display settings found"
echo ""
echo "System default target: $(systemctl get-default)"
echo ""
echo "tsv6-xorg@ service status:"
systemctl status "tsv6-xorg@$USER.service" --no-pager -l 2>/dev/null || echo "Service not found"
echo ""
echo "Display environment:"
echo "DISPLAY: ${DISPLAY:-Not set}"
echo "XAUTHORITY: ${XAUTHORITY:-Not set}"
echo ""
echo "X11 server check:"
if xdpyinfo &>/dev/null; then
    echo "X11 server is running"
    xrandr --query 2>/dev/null || echo "xrandr not available"
else
    echo "X11 server not responding or not started"
fi
EOL
chmod +x "$HOME/display_diagnostics.sh"

# TSV6 control script
cat > "$HOME/tsv6_control.sh" << 'EOL'
#!/bin/bash
# TSV6 Application Control Script
case "$1" in
    start)
        echo "Starting TSV6 services..."
        sudo systemctl start "tsv6-xorg@$USER.service"
        sudo systemctl start "tsv6@$USER.service"
        ;;
    stop)
        echo "Stopping TSV6 services..."
        sudo systemctl stop "tsv6@$USER.service"
        sudo systemctl stop "tsv6-xorg@$USER.service"
        ;;
    restart)
        echo "Restarting TSV6 services..."
        sudo systemctl restart "tsv6@$USER.service"
        ;;
    status)
        echo "=== TSV6 Service Status ==="
        echo ""
        echo "X11 Server:"
        systemctl status "tsv6-xorg@$USER.service" --no-pager -l
        echo ""
        echo "TSV6 Application:"
        systemctl status "tsv6@$USER.service" --no-pager -l
        ;;
    logs)
        echo "=== TSV6 Logs (last 50 lines) ==="
        journalctl -u "tsv6@$USER.service" -n 50 --no-pager
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
EOL
chmod +x "$HOME/tsv6_control.sh"

# STServo test script
cat > "$HOME/test_servo.sh" << 'EOL'
#!/bin/bash
# STServo bus servo test script
echo "=== STServo Bus Servo Test ==="

# Check for USB serial devices
echo "Checking for USB serial devices..."
if ls /dev/ttyUSB* 2>/dev/null; then
    SERVO_PORT=$(ls /dev/ttyUSB* | head -1)
    echo "Found: $SERVO_PORT"
elif ls /dev/ttyACM* 2>/dev/null; then
    SERVO_PORT=$(ls /dev/ttyACM* | head -1)
    echo "Found: $SERVO_PORT"
else
    echo "No USB serial device found - ensure STServo adapter is connected"
    exit 1
fi

# Find project directory
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
if [ -d "$SCRIPT_DIR/tsrpi5" ]; then
    PROJECT_DIR="$SCRIPT_DIR/tsrpi5"
elif [ -d "$HOME/tsrpi5" ]; then
    PROJECT_DIR="$HOME/tsrpi5"
else
    echo "Project directory not found"
    exit 1
fi

cd "$PROJECT_DIR"

# Test pyserial
echo ""
echo "Testing pyserial..."
if [ -f ".venv/bin/python" ]; then
    .venv/bin/python -c "import serial; print('pyserial OK')" || echo "pyserial not available"
else
    python3 -c "import serial; print('pyserial OK')" || echo "pyserial not available"
fi

echo ""
echo "Servo test complete"
EOL
chmod +x "$HOME/test_servo.sh"

success "Diagnostic scripts created in $HOME"

# ============================================================================
# Validation
# ============================================================================
log "Validating installation..."

echo ""
info "Installed services:"
systemctl list-unit-files | grep tsv6 || true

echo ""
info "User groups:"
groups "$CURRENT_USER"

echo ""
info "USB serial devices:"
ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || echo "No USB serial devices found"

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "=================================="
log "Installation Summary"
echo "=================================="
echo ""
info "Services installed and enabled:"
echo "  - tsv6-xorg@$CURRENT_USER.service (X11 server)"
echo "  - tsv6@$CURRENT_USER.service (main application)"
echo ""
info "User added to groups:"
echo "  - dialout (STServo serial access)"
echo "  - input (barcode scanner)"
echo "  - i2c, spi (future sensors)"
echo "  - video (display access)"
echo ""
info "Diagnostic scripts created:"
echo "  - ~/display_diagnostics.sh"
echo "  - ~/tsv6_control.sh"
echo "  - ~/test_servo.sh"
echo ""
warning "IMPORTANT: Log out and back in (or reboot) for group changes to take effect!"
echo ""
info "Optional: For 4G LTE connectivity (Waveshare SIM7600 HAT):"
echo "  Run: sudo ./setup-sim7600.sh"
echo ""
info "Service commands:"
echo "  Start:   sudo systemctl start tsv6@$CURRENT_USER.service"
echo "  Stop:    sudo systemctl stop tsv6@$CURRENT_USER.service"
echo "  Status:  sudo systemctl status tsv6@$CURRENT_USER.service"
echo "  Logs:    journalctl -u tsv6@$CURRENT_USER.service -f"
echo ""
info "Or use: ~/tsv6_control.sh {start|stop|restart|status|logs}"
echo ""

exit 0
