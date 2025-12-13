#!/bin/bash

################################################################################
# TSV6 Raspberry Pi 5 Optimized Setup Script
# Part of: ts_uscup repository
#
# Comprehensive setup script optimized for Raspberry Pi 5 (8GB RAM) deployment.
# Automates the complete installation of TSV6 with hardware-specific optimizations
# for Raspberry Pi 5's enhanced capabilities (2.4GHz CPU, 8GB RAM, VideoCore VII).
#
# Usage: ./tsv6-pi5-setup.sh [github-email] [github-username]
#        GitHub credentials are optional (defaults to factory-droid[bot])
#
# IMPORTANT: This script is optimized for RASPBERRY PI 5 ONLY
#
# Designed for: Raspberry Pi OS Lite (64-bit) - Bookworm
# Hardware: Raspberry Pi 5 (8GB RAM) with Waveshare 7" DSI Display
# Servo: Waveshare ST3020 via Bus Servo Adapter (A) - USB Serial
# Display: Minimal X11 (no display manager) for tkinter support
# Performance Notes:
#   - GPU Memory: 256MB (vs 128MB on Pi 4)
#   - Memory Thresholds: Relaxed for 8GB RAM (75%/85%/92%)
#   - PCIe Gen 3: Enabled for faster I/O
################################################################################

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warning() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

info() {
    echo -e "${BLUE}[INFO] $1${NC}"
}

# Configuration variables
# Hardcoded GitHub credentials - update these as needed
GITHUB_EMAIL="${1:-mwade@genesis1.tech}"
GITHUB_USERNAME="${2:-Marcus Wade}"
DEVICE_ID="${4:-}"
HOSTNAME_PREFIX="tsv6-device"
CERTS_DIR="assets/certs"

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   error "This script should not be run as root. Run as regular user with sudo access."
   exit 1
fi

log "Starting TSV6 Raspberry Pi 5 Complete Setup..."

# ============================================================================
# STEP 1: Pre-Setup Validation
# ============================================================================
log "STEP 1: Running pre-setup validation..."

info "Verifying system requirements..."
if ! command -v sudo &> /dev/null; then
    error "sudo is required but not installed"
    exit 1
fi

# Check for internet connectivity (multiple methods)
info "Checking internet connectivity..."
CONNECTIVITY_OK=false

# Method 1: Try DNS ping (may work if DNS is available)
if ping -c 1 8.8.8.8 &> /dev/null; then
    info "Connectivity confirmed via DNS ping"
    CONNECTIVITY_OK=true
# Method 2: Try HTTP request (works even if ICMP blocked)
elif curl -s --max-time 5 http://httpbin.org/ip &> /dev/null; then
    info "Connectivity confirmed via HTTP request"
    CONNECTIVITY_OK=true
# Method 3: Try HTTPS to common site
elif curl -s --max-time 5 https://www.google.com &> /dev/null; then
    info "Connectivity confirmed via HTTPS"
    CONNECTIVITY_OK=true
# Method 4: Try apt update as final test
elif sudo apt-get update -qq &> /dev/null; then
    info "Connectivity confirmed via package manager"
    CONNECTIVITY_OK=true
fi

if [[ "$CONNECTIVITY_OK" == false ]]; then
    error "Internet connectivity check failed"
    warning "Common causes:"
    warning "  - ICMP blocked (ping not working)"
    warning "  - DNS issues"
    warning "  - Firewall blocking connections"
    warning "  - Network not properly configured"
    warning ""
    warning "Continuing anyway - network may still work for package operations..."
    warning "If issues occur later, check your network configuration."
    # Don't exit - let the script continue
else
    info "Internet connectivity verified"
fi

info "System appears ready for setup"
log "Step 1 completed successfully"

# ============================================================================
# STEP 2: Initial System Update
# ============================================================================
log "STEP 2: Performing initial system update..."

info "Updating package lists..."
sudo apt update

info "Upgrading existing packages..."
sudo apt upgrade -y

log "Step 2 completed successfully"

# ============================================================================
# STEP 3: Initial System Configuration
# ============================================================================
log "STEP 3: Starting Initial System Configuration..."

info "Installing essential packages..."
sudo apt install -y curl wget git vim htop tree ufw fail2ban python3-dev python3-pip build-essential minicom

info "Installing D-Bus Python bindings for systemd recovery..."
sudo apt install -y python3-dbus libdbus-1-dev

info "Installing build tools for AWS IoT SDK compilation..."
sudo apt install -y cmake libssl-dev

info "Installing SDL2 libraries for pygame support..."
sudo apt install -y libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev libportmidi-dev libfreetype6-dev

info "Installing TSV6-specific packages..."

# Try newer package names first, fallback to older ones
# Fix for libatlas-base-dev (replaced by libatlas-base-accel-dev in newer OS)
if sudo apt install -y libatlas-base-accel-dev 2>/dev/null; then
    info "libatlas-base-accel-dev installed (newer package)"
elif sudo apt install -y libatlas-base-dev 2>/dev/null; then
    info "libatlas-base-dev installed (legacy package)"
elif sudo apt install -y libblas-dev liblapack-dev 2>/dev/null; then
    info "libblas-dev and liblapack-dev installed (alternative math libraries)"
else
    warning "Math libraries may not be installed - some ML functions may be slower"
fi

# Fix for python3-tkinter (may be python3-tk in some versions)
if sudo apt install -y python3-tkinter 2>/dev/null; then
    info "python3-tkinter installed"
elif sudo apt install -y python3-tk 2>/dev/null; then
    info "python3-tk installed (alternative package)"
else
    warning "tkinter not available - some GUI features may not work"
fi

# Install VLC and related packages
if sudo apt install -y vlc python3-vlc 2>/dev/null; then
    info "VLC and python3-vlc installed"
else
    warning "VLC installation failed - video playback may not work"
fi

# Install I2C tools
if sudo apt install -y i2c-tools 2>/dev/null; then
    info "I2C tools installed"
else
    warning "I2C tools not available - hardware I2C access may fail"
fi

# Install image libraries
if sudo apt install -y libjpeg-dev zlib1g-dev libpng-dev 2>/dev/null; then
    info "Image processing libraries installed"
else
    warning "Some image processing libraries may be missing"
fi

# Install jq for JSON processing
if sudo apt install -y jq 2>/dev/null; then
    info "jq (JSON processor) installed"
else
    warning "jq not available - JSON processing may be limited"
fi

info "Installing minimal X11 server (no display manager)..."

# Install minimal X11 for tkinter support (NO lightdm, openbox, tint2)
if sudo apt install -y xserver-xorg-core xinit x11-utils 2>/dev/null; then
    info "Minimal X11 server installed (xserver-xorg-core, xinit, x11-utils)"
else
    error "Failed to install minimal X11 server components"
    exit 1
fi

# Install X11 server utilities (xrandr, etc.)
if sudo apt install -y x11-xserver-utils 2>/dev/null; then
    info "X11 server utilities installed"
else
    warning "X11 server utilities not available"
fi

# Install input device tools
sudo apt install -y xinput 2>/dev/null || warning "xinput not available"

info "Installing Python development tools and GPIO libraries..."
sudo apt install -y python3-venv python3-setuptools

# Install GPIO Python system packages
if sudo apt install -y python3-gpiozero 2>/dev/null; then
    info "python3-gpiozero installed"
else
    warning "python3-gpiozero not available - will install via pip"
fi
sudo apt install -y python3-rpi.gpio 2>/dev/null || warning "python3-rpi.gpio not available"

# Install pyserial for STServo bus servo
if sudo apt install -y python3-serial 2>/dev/null; then
    info "python3-serial installed for bus servo control"
else
    warning "python3-serial not available - will install via pip"
fi

info "Configuring system settings..."

# Enable I2C and SPI for hardware interfaces (raspi-config supported)
info "Enabling I2C and SPI interfaces..."
sudo raspi-config nonint do_i2c 0  # 0 = enable
sudo raspi-config nonint do_spi 0  # 0 = enable

# Enable SSH for remote management (raspi-config supported)
info "Enabling SSH..."
sudo raspi-config nonint do_ssh 0  # 0 = enable

# Expand filesystem (raspi-config supported)
info "Expanding filesystem..."
sudo raspi-config nonint do_expand_rootfs

# Configure boot behaviour for console autologin (NOT desktop)
info "Configuring boot behaviour for console autologin..."
sudo raspi-config nonint do_boot_behaviour B2 2>/dev/null || warning "Boot behaviour config may not be supported"

# Disable boot splash for faster boot
info "Disabling boot splash screen..."
sudo raspi-config nonint do_boot_splash 1 2>/dev/null || warning "Boot splash config may not be supported"

# GPU memory split - manually configure (deprecated in raspi-config)
info "Configuring GPU memory split for display operation (Pi 5 optimized)..."
CONFIG_FILE="/boot/firmware/config.txt"
if [ -f "$CONFIG_FILE" ]; then
    # Remove old gpu_mem settings
    sudo sed -i '/^gpu_mem=/d' "$CONFIG_FILE"
    # Add gpu_mem=256 for Pi 5 DSI display (enhanced for 8GB RAM)
    echo "gpu_mem=256" | sudo tee -a "$CONFIG_FILE" > /dev/null
    info "GPU memory set to 256MB (Pi 5 optimized)"
else
    warning "Config file not found at $CONFIG_FILE"
fi

# Network wait at boot - manually configure (deprecated in raspi-config)
info "Disabling network wait at boot..."
if [ -f /etc/systemd/system/dhcpcd.service.d/wait.conf ]; then
    sudo rm /etc/systemd/system/dhcpcd.service.d/wait.conf
fi
sudo systemctl disable systemd-networkd-wait-online.service 2>/dev/null || true

# Configure Waveshare DSI display with Pi 5 optimizations
info "Configuring Waveshare 7-inch DSI display (Pi 5 optimized)..."
CONFIG_FILE="/boot/firmware/config.txt"

# Backup existing config
if [[ -f "$CONFIG_FILE" ]]; then
    sudo cp "$CONFIG_FILE" "$CONFIG_FILE.backup.$(date +%Y%m%d_%H%M%S)"
fi

# Add DSI display configuration with Pi 5 enhancements
sudo tee -a "$CONFIG_FILE" > /dev/null << 'EOL'

# ====================================================================
# TSV6 Waveshare 7-inch DSI Display Configuration (Raspberry Pi 5)
# ====================================================================
dtoverlay=vc4-kms-dsi-waveshare-panel,7inch-1024x600
dtparam=i2c_arm=on
dtparam=spi=on
dtparam=audio=on
disable_overscan=1
framebuffer_width=1024
framebuffer_height=600
hdmi_group=2
hdmi_mode=87
hdmi_cvt=1024 600 60 6 0 0 0

# Power management for stable operation
dtparam=pwr_led_gpio=off
dtparam=act_led_gpio=off

# Additional display optimizations for Pi 5
# Pi 5 specific: Enable PCIe Gen 3 for faster I/O
dtparam=pciex1_gen=3

# GPU memory allocation (256MB for Pi 5 8GB)
gpu_mem=256
max_framebuffers=2

# Contiguous Memory Allocator for GPU
cma=256M@256M
EOL

info "DSI display configuration added to $CONFIG_FILE (Pi 5 optimized)"

# Set hostname with timestamp for uniqueness
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
NEW_HOSTNAME="${HOSTNAME_PREFIX}-${TIMESTAMP}"
info "Setting hostname to: $NEW_HOSTNAME"
sudo raspi-config nonint do_hostname "$NEW_HOSTNAME"

log "Step 3 completed successfully"

# ============================================================================
# STEP 4: Security Hardening
# ============================================================================
log "STEP 4: Starting Security Hardening..."

info "Configuring UFW firewall..."
sudo ufw --force reset
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 8883/tcp  # MQTT over SSL for AWS IoT
sudo ufw allow 443/tcp   # HTTPS for AWS IoT WebSocket
sudo ufw --force enable

info "Configuring fail2ban..."
sudo tee /etc/fail2ban/jail.local > /dev/null <<'EOL'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = ssh
logpath = /var/log/auth.log
maxretry = 3

[nginx-http-auth]
enabled = false
EOL

sudo systemctl enable --now fail2ban

info "Setting up SSH key authentication..."

# Generate device SSH key pair
if [[ ! -f ~/.ssh/id_ed25519 ]]; then
    ssh-keygen -t ed25519 -C "tsv6-device-$(hostname)" -f ~/.ssh/id_ed25519 -N ""
    info "Device SSH key pair generated. Public key:"
    echo "=================================="
    cat ~/.ssh/id_ed25519.pub
    echo "=================================="
fi

# Always generate GitHub SSH key with available credentials
if [[ -n "$GITHUB_EMAIL" && -n "$GITHUB_USERNAME" ]]; then
    info "Generating GitHub SSH key for user: $GITHUB_USERNAME"

    if [[ ! -f ~/.ssh/github_key ]]; then
        ssh-keygen -t ed25519 -C "$GITHUB_EMAIL" -f ~/.ssh/github_key -N ""

        # Create SSH config for GitHub
        mkdir -p ~/.ssh
        tee -a ~/.ssh/config > /dev/null <<EOF

Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_key
  IdentitiesOnly yes
EOF

        chmod 600 ~/.ssh/config

        # Add to ssh-agent
        eval "$(ssh-agent -s)"
        ssh-add ~/.ssh/github_key

        info "GitHub SSH key generated successfully!"
        info "SSH key can be added to GitHub account for push access"
        info "Public key for reference:"
        echo "=================================="
        cat ~/.ssh/github_key.pub
        echo "=================================="
    else
        info "GitHub SSH key already exists"
        # Ensure it's added to ssh-agent
        eval "$(ssh-agent -s)"
        ssh-add ~/.ssh/github_key 2>/dev/null || true
    fi
else
    error "GitHub credentials are missing"
    error "Cannot generate SSH key without GITHUB_EMAIL and GITHUB_USERNAME"
    exit 1
fi

info "Enhancing SSH security configuration..."
# Configure SSH for security
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/g' /etc/ssh/sshd_config
sudo sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/g' /etc/ssh/sshd_config
sudo sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/g' /etc/ssh/sshd_config
sudo sed -i 's/PermitRootLogin yes/PermitRootLogin no/g' /etc/ssh/sshd_config
sudo sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/g' /etc/ssh/sshd_config

warning "SSH password authentication will be disabled. Ensure SSH key access is working!"
warning "Current SSH session will remain active. Test new connection before closing this one."

sudo systemctl restart ssh

log "Step 4 completed successfully"

# ============================================================================
# STEP 5: Python Environment Setup
# ============================================================================
log "STEP 5: Setting up Python Environment..."

info "Installing UV package manager..."
curl -LsSf https://astral.sh/uv/install.sh | sh

# Source UV installation immediately in current shell
source ~/.bashrc 2>/dev/null || true
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Add UV to global PATH for systemd services
echo 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"' | sudo tee -a /etc/environment
echo 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"' >> ~/.bashrc

# Verify UV installation with proper PATH
if command -v uv &> /dev/null || [ -x "$HOME/.local/bin/uv" ] || [ -x "$HOME/.cargo/bin/uv" ]; then
    # Try to find where it installed
    if [ -x "$HOME/.local/bin/uv" ]; then
        UV_BIN="$HOME/.local/bin/uv"
    elif [ -x "$HOME/.cargo/bin/uv" ]; then
        UV_BIN="$HOME/.cargo/bin/uv"
    else
        UV_BIN="uv"
    fi

    UV_VERSION=$($UV_BIN --version 2>/dev/null)
    info "UV installed successfully: $UV_VERSION"

    # Ensure it's in the current PATH for subsequent commands
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        export PATH="$HOME/.local/bin:$PATH"
    fi
else
    error "UV installation failed - UV not found in PATH, ~/.local/bin, or ~/.cargo/bin"
    exit 1
fi

# Create UV environment activation script for systemd services
tee ~/activate_uv_env.sh > /dev/null <<'EOF'
#!/bin/bash
# UV environment activation script for systemd services
source ~/.bashrc
export DISPLAY=:0
export XAUTHORITY=$HOME/.Xauthority

# Find the project directory
if [ -d ~/projects/ts_uscup ]; then
    cd ~/projects/ts_uscup || exit 1
elif [ -d ~/projects/tsv6_rpi ]; then
    cd ~/projects/tsv6_rpi || exit 1
else
    echo "Project directory not found"
    exit 1
fi

source .venv/bin/activate
exec "$@"
EOF

chmod +x ~/activate_uv_env.sh

# Create display environment setup script (for minimal X11)
tee ~/setup_display_env.sh > /dev/null <<EOF
#!/bin/bash
# Display environment setup for TSV6 applications (minimal X11)
export DISPLAY=:0
export XAUTHORITY=\$HOME/.Xauthority

# Wait for X11 server to be ready (tsv6-xorg@ service)
echo "Waiting for X11 server..."
timeout 30 bash -c "until xdpyinfo &>/dev/null; do sleep 1; done"

# Verify display is available
if xrandr &>/dev/null; then
    echo "Display server is ready"
    xrandr --query
else
    echo "Display server not responding"
    exit 1
fi
EOF

chmod +x ~/setup_display_env.sh

info "Creating projects directory..."
mkdir -p ~/projects

# Add user to groups for hardware access
sudo usermod -a -G dialout $USER  # Serial port access for STServo
sudo usermod -a -G i2c $USER
sudo usermod -a -G spi $USER
sudo usermod -a -G input $USER  # Input device access for barcode scanner

# Set system default to multi-user.target (console, not graphical)
info "Setting system default to multi-user.target (console boot)..."
sudo systemctl set-default multi-user.target

# Install TSV6 X11 server service
info "Installing tsv6-xorg@ service for minimal X11..."

# Get the script directory to find the service file
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/tsv6-xorg@.service" ]; then
    sudo cp "$SCRIPT_DIR/tsv6-xorg@.service" /etc/systemd/system/
    sudo chmod 644 /etc/systemd/system/tsv6-xorg@.service
    info "tsv6-xorg@.service installed from project"
else
    # Create the service file inline if not found
    info "Creating tsv6-xorg@.service inline..."
    sudo tee /etc/systemd/system/tsv6-xorg@.service > /dev/null <<'EOL'
[Unit]
Description=TSV6 X11 Server (no display manager)
After=systemd-user-sessions.service
ConditionPathExists=/dev/tty7

[Service]
Type=simple
User=%i
Environment=DISPLAY=:0
ExecStart=/usr/bin/xinit /bin/bash -c "exec sleep infinity" -- :0 vt7 -keeptty -noreset
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOL
    sudo chmod 644 /etc/systemd/system/tsv6-xorg@.service
fi

# Reload and enable the X11 service for current user
sudo systemctl daemon-reload
sudo systemctl enable "tsv6-xorg@$USER.service"
info "tsv6-xorg@$USER.service enabled"

log "Step 5 completed successfully"

# ============================================================================
# STEP 6: Git Configuration and GitHub Setup
# ============================================================================
log "STEP 6: Setting up Git and GitHub..."

if [[ -n "$GITHUB_EMAIL" && -n "$GITHUB_USERNAME" ]]; then
    info "Configuring Git with provided credentials..."
    git config --global user.name "$GITHUB_USERNAME"
    git config --global user.email "$GITHUB_EMAIL"
    git config --global init.defaultBranch main

    # Check if GitHub SSH key is available
    if [[ -f ~/.ssh/github_key ]]; then
        info "GitHub SSH key is available for repository cloning"
    else
        warning "GitHub SSH key not found. Repository cloning will use HTTPS fallback."
    fi
else
    warning "GitHub credentials not provided. Skipping Git configuration."
fi

log "Step 6 completed successfully"

# ============================================================================
# STEP 7: Install TSV6 Python Dependencies
# ============================================================================
log "STEP 7: Installing TSV6 Python dependencies..."

# Ensure project directory exists
mkdir -p ~/projects
if [ -d ~/projects/ts_uscup ]; then
    PROJECT_DIR=~/projects/ts_uscup
elif [ -f "pyproject.toml" ]; then
    PROJECT_DIR=$(pwd)
elif [ -d "ts_uscup" ] && [ -f "ts_uscup/pyproject.toml" ]; then
    PROJECT_DIR=$(realpath ts_uscup)
elif [ -f "$(dirname "$0")/../pyproject.toml" ]; then
    PROJECT_DIR=$(realpath "$(dirname "$0")/..")
else
    warning "Could not determine project root. Assuming current directory."
    PROJECT_DIR=$(pwd)
fi

info "Using project directory: $PROJECT_DIR"
cd "$PROJECT_DIR"

# Create and activate UV venv if not present
if [ ! -d ".venv" ]; then
    info "Creating UV virtual environment..."
    uv venv
fi

# Install dependencies from pyproject.toml
info "Installing dependencies from pyproject.toml via UV..."
uv pip install --python .venv/bin/python -e . || {
    error "Failed to install Python dependencies via UV"
    exit 1
}

# Verify key runtime libraries (no pigpio - using pyserial for STServo)
info "Verifying key Python libraries..."
.venv/bin/python3 - << 'PY'
import importlib, sys, traceback
pkgs = [
    'awsiot','awscrt','psutil','pygame','PIL','vlc','qrcode','serial'
]
missing = []
for p in pkgs:
    try:
        importlib.import_module(p)
    except Exception:
        print(f"Failed to import {p}:")
        traceback.print_exc()
        missing.append(p)
if missing:
    print('Missing Python packages:', ', '.join(missing))
    sys.exit(1)
else:
    print('All key Python packages available')
PY

if [ $? -eq 0 ]; then
    info "Python dependencies verified successfully"
else
    warning "Some Python packages may be missing - check logs above"
fi

log "Step 7 completed successfully"

# ============================================================================
# STEP 8: Create Runtime Directories
# ============================================================================
log "STEP 8: Creating runtime directories for TSV6..."

# Determine the project directory for runtime paths
if [ -n "$PROJECT_DIR" ]; then
    : # Already set
elif [ -d ~/projects/ts_uscup ]; then
    PROJECT_DIR=~/projects/ts_uscup
elif [ -d ~/projects/tsv6_rpi ]; then
    PROJECT_DIR=~/projects/tsv6_rpi
elif [ -f "pyproject.toml" ]; then
    PROJECT_DIR=$(pwd)
elif [ -d "ts_uscup" ] && [ -f "ts_uscup/pyproject.toml" ]; then
    PROJECT_DIR=$(realpath ts_uscup)
else
    PROJECT_DIR=$(pwd)
fi

info "Creating runtime directories in: $PROJECT_DIR"

# Create runtime directories that systemd service expects
mkdir -p "$PROJECT_DIR/data"
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/assets/certs"

# Set appropriate permissions (owned by current user)
chmod 755 "$PROJECT_DIR/data"
chmod 755 "$PROJECT_DIR/logs"
chmod 755 "$PROJECT_DIR/assets/certs"

# Create optional subdirectories within data
mkdir -p "$PROJECT_DIR/data/cache"      # For cached data
mkdir -p "$PROJECT_DIR/data/temp"       # For temporary files
mkdir -p "$PROJECT_DIR/data/state"      # For persistent state

info "Runtime directory structure created:"
info "  - $PROJECT_DIR/data (runtime data)"
info "  - $PROJECT_DIR/logs (application logs)"
info "  - $PROJECT_DIR/assets/certs (AWS IoT certificates)"
info "  - $PROJECT_DIR/data/cache (cached items)"
info "  - $PROJECT_DIR/data/temp (temporary files)"
info "  - $PROJECT_DIR/data/state (persistent state)"

# Verify directories were created
if [ -d "$PROJECT_DIR/data" ] && [ -d "$PROJECT_DIR/logs" ]; then
    info "Runtime directories verified successfully"
else
    error "Failed to create runtime directories"
    exit 1
fi

log "Step 8 completed successfully"

# ============================================================================
# STEP 9: Project Deployment (Manual)
# ============================================================================
log "STEP 9: Project setup - Manual deployment required..."

info "TSV6 project should be cloned manually to ~/projects/tsv6_rpi"
info "After setup completes, run:"
echo "  cd ~/projects"
echo "  git clone https://github.com/genesis1tech/tsv6_rpi.git"
echo "  cd tsv6_rpi"
echo "  uv venv && source .venv/bin/activate"
echo "  uv pip install -r requirements.txt"

log "Step 9 completed - Manual project deployment instructions provided"

# ============================================================================
# STEP 10: AWS IoT Certificate Deployment
# ============================================================================
log "STEP 10: Checking for AWS IoT certificates..."

# Get device serial number for Thing name and device ID generation
DEVICE_SERIAL=$(cat /proc/cpuinfo | grep Serial | cut -d' ' -f2)
if [[ -z "$DEVICE_SERIAL" ]]; then
    warning "Could not retrieve device serial number"
    DEVICE_SERIAL="unknown$(hostname)"
fi

# Extract last 8 characters for Thing name
SERIAL_SUFFIX="${DEVICE_SERIAL: -8}"
SERIAL_SUFFIX_UPPER=$(echo "$SERIAL_SUFFIX" | tr "[:lower:]" "[:upper:]")
THING_NAME="TS_$SERIAL_SUFFIX_UPPER"
DEVICE_ID="TS_$SERIAL_SUFFIX_UPPER"

info "Device Serial: $DEVICE_SERIAL"
info "Generated Thing Name: $THING_NAME"
info "Generated Device ID: $DEVICE_ID"

# Check for certificates in current directory or project directory
# Determine REPO_NAME dynamically
if [ -d ~/projects/ts_uscup ]; then
    REPO_NAME="ts_uscup"
elif [ -d ~/projects/tsv6_rpi ]; then
    REPO_NAME="tsv6_rpi"
else
    REPO_NAME="ts_uscup"  # Default fallback
fi

CERT_LOCATIONS=("." "~/projects/$REPO_NAME" "~/projects/ts_uscup" "~/projects/tsv6_rpi")
CERTS_FOUND=false

for location in "${CERT_LOCATIONS[@]}"; do
    # Expand tilde in path
    expanded_location="${location/#\~/$HOME}"
    if [[ -f "$expanded_location/aws_cert_crt.pem" && -f "$expanded_location/aws_cert_private.pem" ]]; then
        info "AWS IoT certificates found in: $location"
        cd "$expanded_location"
        CERTS_FOUND=true
        break
    fi
done

if [[ "$CERTS_FOUND" == true ]]; then
    info "Deploying AWS IoT certificates to $CERTS_DIR..."

    # Create assets/certs directory within the project
    mkdir -p "$CERTS_DIR"

    # Copy certificate files with proper permissions
    if [[ -f "aws_cert_crt.pem" ]]; then
        cp "aws_cert_crt.pem" "$CERTS_DIR/"
        chmod 644 "$CERTS_DIR/aws_cert_crt.pem"
    fi

    if [[ -f "aws_cert_private.pem" ]]; then
        cp aws_cert_private.pem "$CERTS_DIR/"
        chmod 600 "$CERTS_DIR/aws_cert_private.pem"  # Secure permissions for private key
    fi

    if [[ -f "aws_cert_public.pem" ]]; then
        cp aws_cert_public.pem "$CERTS_DIR/"
        chmod 644 "$CERTS_DIR/aws_cert_public.pem"
    fi

    if [[ -f "aws_cert_ca.pem" ]]; then
        cp aws_cert_ca.pem "$CERTS_DIR/"
        chmod 644 "$CERTS_DIR/aws_cert_ca.pem"
    fi

    if [[ -f "device-config.json" ]]; then
        cp device-config.json "$CERTS_DIR/"
        chmod 644 "$CERTS_DIR/device-config.json"
    fi

    # Verify certificate deployment
    info "Certificate files deployed:"
    ls -la "$CERTS_DIR/"

    log "Step 10: AWS IoT certificates deployed successfully"
else
    warning "No AWS IoT certificates found in expected locations"
    warning "Certificates should be transferred before running this script"
    info "Expected files: aws_cert_crt.pem, aws_cert_private.pem, aws_cert_ca.pem"
    log "Step 10: Skipped (no certificates found)"
fi

# ============================================================================
# Final Setup and System Validation
# ============================================================================
log "Running final system checks..."

info "System resources:"
free -h
df -h

info "UFW status:"
sudo ufw status verbose

info "Fail2ban status:"
sudo systemctl status fail2ban --no-pager

# ============================================================================
# Dependency Validation
# ============================================================================
log "Validating TSV6-specific dependencies..."

info "Checking Python installation..."
python3 --version
python3 -m pip --version

info "Checking UV package manager..."
uv --version

info "Checking VLC installation..."
vlc --version || warning "VLC not found in PATH"

info "Checking I2C tools..."
i2cdetect -v 2>&1 | head -1 || warning "I2C tools not available"

info "Checking Python libraries..."
python3 -c "import tkinter; print('tkinter available')" || warning "tkinter not available"
python3 -c "import vlc; print('python-vlc available')" || warning "python-vlc not available"
python3 -c "import PIL; print('pillow available')" 2>/dev/null || warning "pillow not yet installed"
python3 -c "import serial; print('pyserial available')" 2>/dev/null || warning "pyserial not yet installed"

info "Checking I2C device access..."
if i2cdetect -l 2>/dev/null | grep -q "i2c"; then
    info "I2C devices detected"
    i2cdetect -l
else
    warning "No I2C devices found - check hardware connections"
fi

# Check for USB serial devices (for STServo adapter)
info "Checking for USB serial devices (STServo adapter)..."
if ls /dev/ttyUSB* 2>/dev/null || ls /dev/ttyACM* 2>/dev/null; then
    info "USB serial device(s) detected:"
    ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || true
else
    warning "No USB serial devices found - STServo adapter may not be connected"
fi

log "Dependency validation completed"

# ============================================================================
# Display Validation
# ============================================================================
log "Validating display configuration..."

info "Checking display connectivity..."
if [[ -e /dev/dri/card0 ]]; then
    info "DRM device detected: /dev/dri/card0"
else
    warning "No DRM device found - display may not be connected"
fi

if [[ -e /dev/fb0 ]]; then
    info "Framebuffer device detected: /dev/fb0"
else
    warning "No framebuffer device found"
fi

info "Checking X11 server availability..."
if command -v Xorg &> /dev/null; then
    info "X11 server available"
else
    error "X11 server not found"
fi

info "Checking tsv6-xorg@ service..."
if [ -f /etc/systemd/system/tsv6-xorg@.service ]; then
    info "tsv6-xorg@.service installed"
else
    warning "tsv6-xorg@.service not found"
fi

info "Checking system default target..."
DEFAULT_TARGET=$(systemctl get-default)
if [[ "$DEFAULT_TARGET" == "multi-user.target" ]]; then
    info "System default: $DEFAULT_TARGET (correct - console boot)"
else
    warning "System default: $DEFAULT_TARGET (expected multi-user.target)"
fi

info "Checking display configuration..."
if grep -q "vc4-kms-dsi-waveshare-panel" /boot/firmware/config.txt; then
    info "Waveshare DSI overlay configured"
else
    warning "Waveshare DSI overlay not found in config.txt"
fi

info "Checking GPU memory allocation..."
gpu_memory=$(vcgencmd get_mem gpu | cut -d'=' -f2 | tr -d 'M')
if [[ "$gpu_memory" -ge 64 ]]; then
    info "GPU memory allocated: ${gpu_memory}MB"
else
    warning "GPU memory may be insufficient: ${gpu_memory}MB (recommended: 256MB)"
fi

log "Display validation completed"

# ============================================================================
# Create Systemd Service for Boot Autostart
# ============================================================================
info "Installing TSV6 systemd service..."

# Determine the actual working directory for the project
if [ -d ~/projects/ts_uscup ]; then
    WORK_DIR="$HOME/projects/ts_uscup"
elif [ -d ~/projects/tsv6_rpi ]; then
    WORK_DIR="$HOME/projects/tsv6_rpi"
elif [ -d ~/ts_uscup ]; then
    WORK_DIR="$HOME/ts_uscup"
elif [ -d ~/tsv6_rpi ]; then
    WORK_DIR="$HOME/tsv6_rpi"
else
    WORK_DIR="$HOME/ts_uscup"  # Default fallback
fi

info "Using working directory: $WORK_DIR"

# Install tsv6@.service from project if available
if [ -f "$SCRIPT_DIR/tsv6.service" ]; then
    # Convert to template service
    sed "s|/home/%i/ts_uscup|$WORK_DIR|g" "$SCRIPT_DIR/tsv6.service" | sudo tee /etc/systemd/system/tsv6@.service > /dev/null
    sudo chmod 644 /etc/systemd/system/tsv6@.service
    info "tsv6@.service installed from project"
else
    # Create the service file inline
    sudo tee /etc/systemd/system/tsv6@.service > /dev/null <<EOL
[Unit]
Description=TSV6 Raspberry Pi Video Player
After=network-online.target time-sync.target tsv6-wifi-provisioning.service tsv6-xorg@%i.service
Wants=network-online.target tsv6-xorg@%i.service
Requires=tsv6-wifi-provisioning.service

[Service]
Type=simple
User=%i
Group=%i
WorkingDirectory=$WORK_DIR
Environment="PATH=/home/%i/.local/bin:/home/%i/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
Environment="DISPLAY=:0"
Environment="XAUTHORITY=/home/%i/.Xauthority"
Environment="TSV6_ENVIRONMENT=production"
ExecStartPre=/bin/sleep 5
ExecStart=/home/%i/.local/bin/uv run python run_production.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tsv6

[Install]
WantedBy=multi-user.target
EOL
    sudo chmod 644 /etc/systemd/system/tsv6@.service
fi

# Enable the service to start on boot
info "Enabling TSV6 service to start on boot..."
sudo systemctl daemon-reload
sudo systemctl enable "tsv6@$USER.service"

info "Systemd service created and enabled for boot autostart"

# Determine REPO_NAME dynamically
if [ -d ~/projects/ts_uscup ]; then
    REPO_NAME="ts_uscup"
elif [ -d ~/projects/tsv6_rpi ]; then
    REPO_NAME="tsv6_rpi"
else
    REPO_NAME="ts_uscup"  # Default fallback
fi

# ============================================================================
# Create Maintenance and Diagnostic Scripts
# ============================================================================
log "Creating maintenance and diagnostic scripts..."

# Create TSV6 application control script
tee ~/tsv6_control.sh > /dev/null <<'EOL'
#!/bin/bash
# TSV6 Application Control Script

REPO_DIR="$HOME/projects/$(ls $HOME/projects | head -1 2>/dev/null || echo 'tsv6_rpi')"
cd "$REPO_DIR" 2>/dev/null || { echo "Project directory not found"; exit 1; }

case "$1" in
    start)
        echo "Starting TSV6 application..."
        if [[ -f ".venv/bin/activate" && -f "main.py" ]]; then
            source .venv/bin/activate
            export DISPLAY=:0
            python3 main.py &
            echo "TSV6 application started"
        else
            echo "TSV6 application files not found"
        fi
        ;;
    stop)
        echo "Stopping TSV6 application..."
        pkill -f "python3 main.py" || echo "No TSV6 application running"
        ;;
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;
    status)
        if pgrep -f "python3 main.py" > /dev/null; then
            echo "TSV6 application is running"
            echo "PIDs: $(pgrep -f 'python3 main.py')"
        else
            echo "TSV6 application is not running"
        fi
        ;;
    logs)
        echo "TSV6 Application Logs (if available):"
        if [[ -d "logs" ]]; then
            ls -la logs/
            if [[ -f "logs/tsv6.log" ]]; then
                echo "Recent log entries:"
                tail -20 logs/tsv6.log
            fi
        else
            echo "No logs directory found"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
EOL

chmod +x ~/tsv6_control.sh

# Create display diagnostic script (updated for minimal X11)
tee ~/display_diagnostics.sh > /dev/null <<'EOL'
#!/bin/bash
# TSV6 Display diagnostics script (minimal X11)
echo "=== TSV6 Display Diagnostics ==="
echo "Date: $(date)"
echo ""
echo "GPU Memory: $(vcgencmd get_mem gpu)"
echo "Framebuffer devices: $(ls -la /dev/fb* 2>/dev/null || echo 'None found')"
echo "DRM devices: $(ls -la /dev/dri/* 2>/dev/null || echo 'None found')"
echo ""
echo "Config.txt DSI settings:"
grep -E "(dtoverlay.*waveshare|framebuffer|hdmi_|gpu_mem)" /boot/firmware/config.txt || echo "No display settings found"
echo ""
echo "System default target:"
systemctl get-default
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

chmod +x ~/display_diagnostics.sh

# Create advanced display troubleshooting script (updated for minimal X11)
tee ~/fix_display.sh > /dev/null <<'EOL'
#!/bin/bash
# Display troubleshooting and recovery script (minimal X11)
echo "=== Display Troubleshooting ==="

# Function to fix common display issues
fix_display() {
    echo "Attempting to fix display issues..."

    # Restart X11 service
    echo "Restarting tsv6-xorg@ service..."
    sudo systemctl restart "tsv6-xorg@$USER.service"

    # Wait for X server to start
    sleep 5

    # Check if display is working
    if xdpyinfo &>/dev/null; then
        echo "Display restored successfully"
        xrandr --query
    else
        echo "Display still not responding"
        echo "Check service status: sudo systemctl status tsv6-xorg@$USER.service"
    fi
}

# Check current display status
echo "Current display status:"
if [[ -e /dev/dri/card0 ]]; then
    echo "DRM device: /dev/dri/card0"
else
    echo "No DRM device found"
fi

if [[ -e /dev/fb0 ]]; then
    echo "Framebuffer: /dev/fb0"
else
    echo "No framebuffer found"
fi

# Check services
echo ""
echo "Service status:"
systemctl is-active "tsv6-xorg@$USER.service" && echo "tsv6-xorg@: Active" || echo "tsv6-xorg@: Inactive"
echo "System default: $(systemctl get-default)"

# Ask user if they want to fix
echo ""
read -p "Attempt to fix display issues? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    fix_display
else
    echo "Display troubleshooting complete"
fi

echo ""
echo "Additional commands to try manually:"
echo "- sudo reboot (if display still not working)"
echo "- sudo systemctl restart tsv6-xorg@$USER.service"
echo "- ~/display_diagnostics.sh (run diagnostics again)"
EOL

chmod +x ~/fix_display.sh

# Create backup script
tee ~/backup_tsv6.sh > /dev/null <<'EOL'
#!/bin/bash
# TSV6 Automated backup script
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$HOME/backups"
mkdir -p "$BACKUP_DIR"

echo "Creating TSV6 backup: $DATE"

tar -czf "$BACKUP_DIR/tsv6_backup_$DATE.tar.gz" \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='logs/*.log' \
    ~/.ssh \
    ~/.bashrc \
    ~/.gitconfig \
    ~/projects 2>/dev/null

echo "Backup created: $BACKUP_DIR/tsv6_backup_$DATE.tar.gz"
echo "Backup size: $(du -h $BACKUP_DIR/tsv6_backup_$DATE.tar.gz | cut -f1)"
EOL

chmod +x ~/backup_tsv6.sh

# Create STServo test script
tee ~/test_servo.sh > /dev/null <<'EOL'
#!/bin/bash
# STServo bus servo test script
echo "=== STServo Bus Servo Test ==="

# Find project directory
if [ -d ~/projects/ts_uscup ]; then
    PROJECT_DIR=~/projects/ts_uscup
elif [ -d ~/projects/tsv6_rpi ]; then
    PROJECT_DIR=~/projects/tsv6_rpi
else
    echo "Project directory not found"
    exit 1
fi

cd "$PROJECT_DIR"

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

# Test pyserial
echo ""
echo "Testing pyserial..."
if [ -f ".venv/bin/python" ]; then
    .venv/bin/python -c "import serial; print('pyserial OK')" || echo "pyserial not available"
else
    python3 -c "import serial; print('pyserial OK')" || echo "pyserial not available"
fi

# Test servo connection
echo ""
echo "Testing STServo connection..."
if [ -f ".venv/bin/python" ]; then
    .venv/bin/python -c "
from tsv6.hardware.stservo import STServoController
try:
    servo = STServoController(port='$SERVO_PORT', simulation_mode=False)
    if servo._connected:
        print('STServo connected successfully!')
        print(f'Port: {servo.port}')
        print(f'Baudrate: {servo.baudrate}')
    else:
        print('STServo running in simulation mode')
    servo.cleanup()
except Exception as e:
    print(f'Error: {e}')
"
else
    echo "Virtual environment not found - run from project directory with venv activated"
fi

echo ""
echo "Servo test complete"
EOL

chmod +x ~/test_servo.sh

log "Maintenance scripts created successfully"

# ============================================================================
# STEP 11: AWS IoT Certificate Provisioner
# ============================================================================
log "STEP 11: Running AWS IoT Certificate Provisioner..."

# Check if project directory exists and has the provisioner script
if [ -d "$PROJECT_DIR" ]; then
    cd "$PROJECT_DIR"

    if [ -f "aws-iot-cert-provisioner.py" ]; then
        info "Found AWS IoT certificate provisioner script"

        # Activate virtual environment
        if [ -f ".venv/bin/activate" ]; then
            source .venv/bin/activate

            info "Running certificate provisioner..."
            info "Device ID: $DEVICE_ID"
            info "Thing Name: $THING_NAME"

            # Run the provisioner
            if python3 aws-iot-cert-provisioner.py; then
                info "AWS IoT certificate provisioner completed successfully"

                # Check if certificates were created
                if [ -f "assets/certs/aws_cert_crt.pem" ] && [ -f "assets/certs/aws_cert_private.pem" ]; then
                    info "AWS IoT certificates generated and deployed"
                    CERTS_FOUND=true
                else
                    warning "Certificate provisioner ran but certificates not found in expected location"
                fi
            else
                warning "AWS IoT certificate provisioner failed or was skipped"
                warning "You may need to run it manually: python3 aws-iot-cert-provisioner.py"
            fi

            deactivate
        else
            warning "Virtual environment not found - skipping certificate provisioner"
        fi
    else
        warning "AWS IoT certificate provisioner script not found"
        warning "Expected location: $PROJECT_DIR/aws-iot-cert-provisioner.py"
        info "You can run it manually after cloning the project"
    fi
else
    warning "Project directory not found - skipping certificate provisioner"
    info "Run the provisioner manually after setting up the project:"
    info "  cd ~/projects/ts_uscup"
    info "  source .venv/bin/activate"
    info "  python3 aws-iot-cert-provisioner.py"
fi

log "Step 11 completed"

# ============================================================================
# FINAL SUMMARY AND NEXT STEPS
# ============================================================================
log "=========================================="
log "TSV6 Raspberry Pi 5 Setup Complete!"
log "=========================================="

info "Summary of completed tasks:"
info "  System updated and TSV6-specific packages installed"
info "  Security hardening (firewall, fail2ban, SSH keys)"
info "  Python environment with UV package manager configured"
info "  Minimal X11 server configured (no display manager)"
info "  Display system configured for Waveshare 7-inch DSI"
info "  Git and GitHub SSH keys generated"
info "  User added to dialout group for serial access (STServo)"
if [[ -n "$GITHUB_REPO_URL" ]]; then
    info "  TSV6 project cloned and environment set up"
fi
if [[ "$CERTS_FOUND" == true ]]; then
    info "  AWS IoT certificates deployed to $CERTS_DIR"
fi
info "  Comprehensive diagnostics and maintenance scripts created"
info "  TSV6 systemd service enabled for automatic boot startup"
info "  AWS IoT certificate provisioner executed"

warning "CRITICAL NEXT STEPS:"
if [[ -n "$GITHUB_EMAIL" ]]; then
    warning "1. Add the GitHub SSH key (shown above) to your GitHub account"
    warning "2. Test GitHub connection: ssh -T git@github.com"
fi
warning "3. Configure your .env file with TSV6-specific settings"
warning "4. Connect STServo adapter to USB port"
warning "5. REBOOT THE SYSTEM: sudo reboot"
warning "   TSV6 will automatically start on boot - no login required!"
warning "   This is required for display configuration and group membership"

info ""
info "After reboot, useful commands:"
info "  Display diagnostics: ~/display_diagnostics.sh"
info "  Display troubleshooting: ~/fix_display.sh"
info "  Test STServo: ~/test_servo.sh"
info "  Control TSV6 app: ~/tsv6_control.sh {start|stop|restart|status|logs}"
info "  Backup system: ~/backup_tsv6.sh"
info "  Run AWS IoT provisioner: cd ~/projects/ts_uscup && source .venv/bin/activate && python3 aws-iot-cert-provisioner.py"
info "  TSV6 auto-starts on boot (no manual commands needed)"
info "  Control service: sudo systemctl {start|stop|restart|status} tsv6@$USER.service"

info ""
info "Important file locations:"
info "  Project: ~/projects/$REPO_NAME"
info "  Certificates: $CERTS_DIR (if deployed)"
info "  Config: /boot/firmware/config.txt"

info ""
info "Device information:"
info "  Hostname: $NEW_HOSTNAME"
info "  Thing Name: $THING_NAME"
info "  Boot target: multi-user.target (console with X11 service)"
info "  X11 service: tsv6-xorg@$USER.service"

log "TSV6 setup completed successfully!"
log "Remember to reboot: sudo reboot"
