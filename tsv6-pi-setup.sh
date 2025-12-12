#!/bin/bash

################################################################################
# TSV6 Raspberry Pi Complete Setup Script
# Part of: raspberry-pi-startup repository
# 
# Comprehensive setup script that automates the complete deployment of TSV6
# devices on Raspberry Pi hardware. Downloads TSV6 project from GitHub and
# configures everything for production deployment.
#
# Usage: ./tsv6-pi-setup.sh [github-email] [github-username]
#        GitHub credentials are optional (defaults to factory-droid[bot])
#
# Designed for: Raspberry Pi OS Lite (64-bit) - Bookworm
# Hardware: Raspberry Pi 4B/5 with Waveshare 7" DSI Display
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

log "Starting TSV6 Raspberry Pi Complete Setup..."

# ============================================================================
# IDEMPOTENCY CHECK - Prevent multiple setups
# ============================================================================
SETUP_MARKER="/etc/tsv6-setup-complete"

if [[ -f "$SETUP_MARKER" ]]; then
    warning "System appears to already be configured"
    warning "Setup marker file found: $SETUP_MARKER"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Setup skipped - system already configured"
        exit 0
    fi
fi

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
    info "✓ Connectivity confirmed via DNS ping"
    CONNECTIVITY_OK=true
# Method 2: Try HTTP request (works even if ICMP blocked)
elif curl -s --max-time 5 http://httpbin.org/ip &> /dev/null; then
    info "✓ Connectivity confirmed via HTTP request"
    CONNECTIVITY_OK=true
# Method 3: Try HTTPS to common site
elif curl -s --max-time 5 https://www.google.com &> /dev/null; then
    info "✓ Connectivity confirmed via HTTPS"
    CONNECTIVITY_OK=true
# Method 4: Try apt update as final test
elif sudo apt-get update -qq &> /dev/null; then
    info "✓ Connectivity confirmed via package manager"
    CONNECTIVITY_OK=true
fi

if [[ "$CONNECTIVITY_OK" == false ]]; then
    error "⚠ Internet connectivity check failed"
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
    info "✓ Internet connectivity verified"
fi

info "System appears ready for setup"
log "✓ Step 1 completed successfully"

# ============================================================================
# STEP 2: Initial System Update
# ============================================================================
log "STEP 2: Performing initial system update..."

info "Updating package lists..."
sudo apt update

info "Upgrading existing packages..."
sudo apt upgrade -y

log "✓ Step 2 completed successfully"

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
    info "✓ libatlas-base-accel-dev installed (newer package)"
elif sudo apt install -y libatlas-base-dev 2>/dev/null; then
    info "✓ libatlas-base-dev installed (legacy package)"
elif sudo apt install -y libblas-dev liblapack-dev 2>/dev/null; then
    info "✓ libblas-dev and liblapack-dev installed (alternative math libraries)"
else
    warning "⚠ Math libraries may not be installed - some ML functions may be slower"
fi

# Fix for python3-tkinter (may be python3-tk in some versions)
if sudo apt install -y python3-tkinter 2>/dev/null; then
    info "✓ python3-tkinter installed"
elif sudo apt install -y python3-tk 2>/dev/null; then
    info "✓ python3-tk installed (alternative package)"
else
    warning "⚠ tkinter not available - some GUI features may not work"
fi

# Install VLC and related packages
if sudo apt install -y vlc python3-vlc 2>/dev/null; then
    info "✓ VLC and python3-vlc installed"
else
    warning "⚠ VLC installation failed - video playback may not work"
fi

# Install I2C tools
if sudo apt install -y i2c-tools 2>/dev/null; then
    info "✓ I2C tools installed"
else
    warning "⚠ I2C tools not available - hardware I2C access may fail"
fi

# Install image libraries
if sudo apt install -y libjpeg-dev zlib1g-dev libpng-dev 2>/dev/null; then
    info "✓ Image processing libraries installed"
else
    warning "⚠ Some image processing libraries may be missing"
fi

info "Installing display server components..."

# Install core display components
if sudo apt install -y xorg lightdm openbox 2>/dev/null; then
    info "✓ Core display server (xorg, lightdm, openbox) installed"
else
    error "❌ Failed to install core display server components"
    exit 1
fi

# Install desktop environment components (with fallbacks)
sudo apt install -y tint2 2>/dev/null || warning "⚠ tint2 (taskbar) not available"

# Install X11 utilities (xrandr is often included in x11-xserver-utils)
if sudo apt install -y x11-xserver-utils 2>/dev/null; then
    info "✓ X11 server utilities installed"
elif sudo apt install -y x11-utils 2>/dev/null; then
    info "✓ X11 utilities installed (alternative package)"
else
    warning "⚠ X11 utilities not available - display configuration may be limited"
fi

# Install input device tools
sudo apt install -y xinput 2>/dev/null || warning "⚠ xinput not available"

# Install cursor hiding utility
sudo apt install -y unclutter 2>/dev/null || warning "⚠ unclutter not available"

info "Installing Python development tools and GPIO libraries..."
sudo apt install -y python3-venv python3-setuptools

# Install GPIO Python system packages
if sudo apt install -y python3-gpiozero 2>/dev/null; then
    info "✓ python3-gpiozero installed"
else
    warning "⚠ python3-gpiozero not available - will install via pip"
fi
sudo apt install -y python3-rpi.gpio 2>/dev/null || warning "⚠ python3-rpi.gpio not available"

info "Installing AWS CLI v2..."
if ! command -v aws &> /dev/null; then
    TEMP_DIR=$(mktemp -d)
    cd "$TEMP_DIR"
    info "Downloading AWS CLI v2 for ARM64..."
    curl -s "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip"
    info "Extracting AWS CLI..."
    unzip -q awscliv2.zip
    info "Installing AWS CLI..."
    sudo ./aws/install
    cd - > /dev/null
    rm -rf "$TEMP_DIR"
    info "✓ AWS CLI v2 installed successfully"
else
    info "✓ AWS CLI already installed"
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

# Configure boot behaviour for desktop autologin (raspi-config supported)
info "Configuring boot behaviour for display..."
sudo raspi-config nonint do_boot_behaviour B2 2>/dev/null || warning "Boot behaviour config may not be supported"

# Configure boot splash (raspi-config supported)
info "Configuring boot splash screen..."
sudo raspi-config nonint do_boot_splash 1 2>/dev/null || warning "Boot splash config may not be supported"

# GPU memory split - manually configure (deprecated in raspi-config)
info "Configuring GPU memory split for display operation..."
CONFIG_FILE="/boot/firmware/config.txt"
if [ -f "$CONFIG_FILE" ]; then
    # Remove old gpu_mem settings
    sudo sed -i '/^gpu_mem=/d' "$CONFIG_FILE"
    # Add gpu_mem=128 for DSI display
    echo "gpu_mem=128" | sudo tee -a "$CONFIG_FILE" > /dev/null
    info "✓ GPU memory set to 128MB"
else
    warning "Config file not found at $CONFIG_FILE"
fi

# Network wait at boot - manually configure (deprecated in raspi-config)
info "Disabling network wait at boot..."
if [ -f /etc/systemd/system/dhcpcd.service.d/wait.conf ]; then
    sudo rm /etc/systemd/system/dhcpcd.service.d/wait.conf
fi
sudo systemctl disable systemd-networkd-wait-online.service 2>/dev/null || true

# Configure Waveshare DSI display
info "Configuring Waveshare 7-inch DSI display..."
CONFIG_FILE="/boot/firmware/config.txt"

# Backup existing config
if [[ -f "$CONFIG_FILE" ]]; then
    sudo cp "$CONFIG_FILE" "$CONFIG_FILE.backup.$(date +%Y%m%d_%H%M%S)"
fi

# Add DSI display configuration
sudo tee -a "$CONFIG_FILE" > /dev/null << 'EOL'

# ====================================================================
# TSV6 Waveshare 7-inch DSI Display Configuration
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

# Additional display optimizations
gpu_mem=128
max_framebuffers=2
EOL

info "DSI display configuration added to $CONFIG_FILE"

# Set hostname with timestamp for uniqueness
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
NEW_HOSTNAME="${HOSTNAME_PREFIX}-${TIMESTAMP}"
info "Setting hostname to: $NEW_HOSTNAME"
sudo raspi-config nonint do_hostname "$NEW_HOSTNAME"

log "✓ Step 3 completed successfully"

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
        
        info "✓ GitHub SSH key generated successfully!"
        info "✓ SSH key can be added to GitHub account for push access"
        info "Public key for reference:"
        echo "=================================="
        cat ~/.ssh/github_key.pub
        echo "=================================="
    else
        info "✓ GitHub SSH key already exists"
        # Ensure it's added to ssh-agent
        eval "$(ssh-agent -s)"
        ssh-add ~/.ssh/github_key 2>/dev/null || true
    fi
else
    error "❌ GitHub credentials are missing"
    error "❌ Cannot generate SSH key without GITHUB_EMAIL and GITHUB_USERNAME"
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

log "✓ Step 4 completed successfully"

# ============================================================================
# STEP 5: Python Environment Setup
# ============================================================================
log "STEP 5: Setting up Python Environment..."

info "Installing UV package manager..."
curl -LsSf https://astral.sh/uv/install.sh | sh

# Wait a moment for installation to complete
sleep 2

# Source UV installation immediately in current shell
source ~/.bashrc 2>/dev/null || true
source ~/.local/bin/env 2>/dev/null || true
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Add UV to global PATH for systemd services (check both possible locations)
echo 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"' | sudo tee -a /etc/environment
echo 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"' >> ~/.bashrc

# Verify UV installation with proper PATH (check both locations)
if command -v uv &> /dev/null || [ -x "$HOME/.local/bin/uv" ] || [ -x "$HOME/.cargo/bin/uv" ]; then
    UV_VERSION=$(uv --version 2>/dev/null || $HOME/.local/bin/uv --version 2>/dev/null || $HOME/.cargo/bin/uv --version 2>/dev/null)
    info "UV installed successfully: $UV_VERSION"
    info "UV location: $(which uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
else
    error "UV installation failed - UV not found in PATH, ~/.local/bin, or ~/.cargo/bin"
    info "Checking installation locations:"
    ls -la "$HOME/.local/bin/uv" 2>/dev/null || echo "  Not in ~/.local/bin"
    ls -la "$HOME/.cargo/bin/uv" 2>/dev/null || echo "  Not in ~/.cargo/bin"
    exit 1
fi

# Create UV environment activation script for systemd services
tee ~/activate_uv_env.sh > /dev/null <<EOF
#!/bin/bash
# UV environment activation script for systemd services
source ~/.bashrc
export DISPLAY=:0
export XAUTHORY=\$HOME/.Xauthority
cd ~/projects/\$(basename "$GITHUB_REPO_SSH" .git) || exit 1
source .venv/bin/activate
exec "\$@"
EOF

chmod +x ~/activate_uv_env.sh

# Create display environment setup script
tee ~/setup_display_env.sh > /dev/null <<EOF
#!/bin/bash
# Display environment setup for TSV6 applications
export DISPLAY=:0
export XAUTHORY=\$HOME/.Xauthority

# Wait for X11 server to be ready
echo "Waiting for X11 server..."
timeout 30 bash -c "until [[ -e \$XAUTHORITY ]]; do sleep 1; done"

# Verify display is available
if xrandr &>/dev/null; then
    echo "✓ Display server is ready"
    xrandr --query
else
    echo "⚠ Display server not responding"
    exit 1
fi
EOF

chmod +x ~/setup_display_env.sh

info "Creating projects directory..."
mkdir -p ~/projects

# Add user to groups for hardware access
sudo usermod -a -G dialout $USER
sudo usermod -a -G i2c $USER
sudo usermod -a -G spi $USER

# Configure display manager for auto-login
info "Configuring display manager for auto-login..."
sudo systemctl enable lightdm
sudo systemctl set-default graphical.target

# Configure auto-login for current user
sudo tee /etc/lightdm/lightdm.conf > /dev/null << EOF
[SeatDefaults]
autologin-user=$USER
autologin-user-timeout=0
autologin-session=openbox-session
user-session=openbox
EOF

log "✓ Step 5 completed successfully"

# ============================================================================
# STEP 6: pigpio Installation for GPIO Control
# ============================================================================
log "STEP 6: Installing pigpio for GPIO servo control..."

info "Installing pigpio system packages..."
sudo apt update
sudo apt install -y pigpio-tools python3-pigpio libpigpiod-if2-1t64 libpigpiod-if-dev

info "Installing pigpio daemon from source..."
cd /tmp
wget -q https://github.com/joan2937/pigpio/archive/master.zip
unzip -q master.zip
cd pigpio-master

sudo make -j$(nproc) > /dev/null 2>&1
sudo make install > /dev/null 2>&1

info "Configuring pigpiod service..."
sudo cp util/pigpiod.service /etc/systemd/system/
sudo sed -i 's|/usr/bin/pigpiod|/usr/local/bin/pigpiod|' /etc/systemd/system/pigpiod.service

sudo systemctl daemon-reload
sudo systemctl enable pigpiod
sudo systemctl start pigpiod

if systemctl is-active --quiet pigpiod; then
    info "✓ pigpiod service is running"
else
    warning "pigpiod service failed to start"
fi

info "Testing pigpio Python library..."
if python3 -c "import pigpio; pi = pigpio.pi(); print('✓ Connected:', pi.connected); pi.stop()" 2>/dev/null; then
    info "✓ pigpio test successful"
fi

info "GPIO18 servo ready: Pin 18 (Physical 12), 50Hz PWM"

cd /home/pi 2>/dev/null || cd ~
sudo rm -rf /tmp/pigpio-master /tmp/master.zip 2>/dev/null || true

log "✓ Step 6 completed successfully"

# STEP 7: Git Configuration and GitHub Setup
# ============================================================================
log "STEP 7: Setting up Git and GitHub..."

if [[ -n "$GITHUB_EMAIL" && -n "$GITHUB_USERNAME" ]]; then
    info "Configuring Git with provided credentials..."
    git config --global user.name "$GITHUB_USERNAME"
    git config --global user.email "$GITHUB_EMAIL"
    git config --global init.defaultBranch main
    
    # Check if GitHub SSH key is available
    if [[ -f ~/.ssh/github_key ]]; then
        info "✓ GitHub SSH key is available for repository cloning"
    else
        warning "GitHub SSH key not found. Repository cloning will use HTTPS fallback."
    fi
else
    warning "GitHub credentials not provided. Skipping Git configuration."
fi

log "✓ Step 6 completed successfully"

# ============================================================================
# STEP 7.5: Install TSV6 Python Dependencies
# ============================================================================
log "STEP 7.5: Installing TSV6 Python dependencies..."

# Ensure project directory exists
mkdir -p ~/projects
if [ -d ~/projects/ts_uscup ]; then
    PROJECT_DIR=~/projects/ts_uscup
else
    PROJECT_DIR=$(pwd)
fi

info "Using project directory: $PROJECT_DIR"
cd "$PROJECT_DIR"

# Create and activate UV venv if not present
if [ ! -d ".venv" ]; then
    info "Creating UV virtual environment..."
    # Try uv from PATH first, then fallback to specific locations
    if command -v uv &> /dev/null; then
        uv venv
    elif [ -x "$HOME/.local/bin/uv" ]; then
        ~/.local/bin/uv venv
    elif [ -x "$HOME/.cargo/bin/uv" ]; then
        ~/.cargo/bin/uv venv
    else
        error "UV not found in PATH, ~/.local/bin, or ~/.cargo/bin"
        exit 1
    fi
fi

# Install dependencies from pyproject.toml
info "Installing dependencies from pyproject.toml via UV..."
if command -v uv &> /dev/null; then
    uv pip install -e . || {
        error "Failed to install Python dependencies via UV"
        exit 1
    }
elif [ -x "$HOME/.local/bin/uv" ]; then
    ~/.local/bin/uv pip install -e . || {
        error "Failed to install Python dependencies via UV"
        exit 1
    }
elif [ -x "$HOME/.cargo/bin/uv" ]; then
    ~/.cargo/bin/uv pip install -e . || {
        error "Failed to install Python dependencies via UV"
        exit 1
    }
else
    error "UV not found in PATH, ~/.local/bin, or ~/.cargo/bin"
    exit 1
fi

# Verify key runtime libraries
info "Verifying key Python libraries..."
.venv/bin/python3 - << 'PY'
import importlib, sys
pkgs = [
    'awsiotsdk','awscrt','psutil','pygame','PIL','vlc','qrcode','pigpio'
]
missing = []
for p in pkgs:
    try:
        importlib.import_module(p)
    except Exception:
        missing.append(p)
if missing:
    print('⚠ Missing Python packages:', ', '.join(missing))
    sys.exit(1)
else:
    print('✓ All key Python packages available')
PY

if [ $? -eq 0 ]; then
    info "✓ Python dependencies verified successfully"
else
    warning "Some Python packages may be missing - check logs above"
fi

log "✓ Step 7.5 completed successfully"

# ============================================================================
# STEP 7.7: Create Runtime Directories
# ============================================================================
log "STEP 7.7: Creating runtime directories for TSV6..."

# Determine the project directory for runtime paths
if [ -d ~/projects/ts_uscup ]; then
    PROJECT_DIR=~/projects/ts_uscup
elif [ -d ~/projects/tsv6_rpi ]; then
    PROJECT_DIR=~/projects/tsv6_rpi
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

info "✓ Runtime directory structure created:"
info "  - $PROJECT_DIR/data (runtime data)"
info "  - $PROJECT_DIR/logs (application logs)"
info "  - $PROJECT_DIR/assets/certs (AWS IoT certificates)"
info "  - $PROJECT_DIR/data/cache (cached items)"
info "  - $PROJECT_DIR/data/temp (temporary files)"
info "  - $PROJECT_DIR/data/state (persistent state)"

# Verify directories were created
if [ -d "$PROJECT_DIR/data" ] && [ -d "$PROJECT_DIR/logs" ]; then
    info "✓ Runtime directories verified successfully"
else
    error "Failed to create runtime directories"
    exit 1
fi

log "✓ Step 7.7 completed successfully"

# ============================================================================

# ============================================================================
# STEP 8: Project Deployment (Manual)
# ============================================================================
log "STEP 8: Project setup - Manual deployment required..."

info "TSV6 project should be cloned manually to ~/projects/tsv6_rpi"
info "After setup completes, run:"
echo "  cd ~/projects"
echo "  git clone https://github.com/genesis1tech/tsv6_rpi.git"
echo "  cd tsv6_rpi"
echo "  uv venv && source .venv/bin/activate"
echo "  uv pip install -r requirements.txt"

log "✓ Step 8 completed - Manual project deployment instructions provided"

# STEP 9: AWS IoT Certificate Deployment
# ============================================================================
log "STEP 9: Checking for AWS IoT certificates..."

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
CERT_LOCATIONS=("." "~/projects/$REPO_NAME" "~/projects/tsv6_rpi")
CERTS_FOUND=false

for location in "${CERT_LOCATIONS[@]}"; do
    if [[ -f "$location/aws_cert_crt.pem" && -f "$location/aws_cert_private.pem" ]]; then
        info "AWS IoT certificates found in: $location"
        cd "$location"
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
        cp aws_cert_crt.pem "$CERTS_DIR/"
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
    
    log "✓ Step 8: AWS IoT certificates deployed successfully"
else
    warning "No AWS IoT certificates found in expected locations"
    warning "Certificates should be transferred before running this script"
    info "Expected files: aws_cert_crt.pem, aws_cert_private.pem, aws_cert_ca.pem"
    log "✓ Step 8: Skipped (no certificates found)"
fi

# ============================================================================
# Step 9: Skip DFRobot HAT (removed as requested)
# ============================================================================

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
python3 -c "import tkinter; print('✓ tkinter available')" || warning "tkinter not available"
python3 -c "import vlc; print('✓ python-vlc available')" || warning "python-vlc not available"
python3 -c "import PIL; print('✓ pillow available')" 2>/dev/null || warning "pillow not yet installed"

info "Checking I2C device access..."
if i2cdetect -l 2>/dev/null | grep -q "i2c"; then
    info "✓ I2C devices detected"
    i2cdetect -l
else
    warning "No I2C devices found - check hardware connections"
fi

log "✓ Dependency validation completed"

# ============================================================================
# Display Validation
# ============================================================================
log "Validating display configuration..."

info "Checking display connectivity..."
if [[ -e /dev/dri/card0 ]]; then
    info "✓ DRM device detected: /dev/dri/card0"
else
    warning "No DRM device found - display may not be connected"
fi

if [[ -e /dev/fb0 ]]; then
    info "✓ Framebuffer device detected: /dev/fb0"
else
    warning "No framebuffer device found"
fi

info "Checking X11 server status..."
if command -v Xorg &> /dev/null; then
    info "✓ X11 server available"
else
    error "X11 server not found"
fi

info "Checking display manager..."
if systemctl is-active --quiet lightdm; then
    info "✓ LightDM display manager is active"
else
    warning "LightDM display manager not active"
fi

info "Checking display configuration..."
if grep -q "vc4-kms-dsi-waveshare-panel" /boot/firmware/config.txt; then
    info "✓ Waveshare DSI overlay configured"
else
    warning "Waveshare DSI overlay not found in config.txt"
fi

info "Checking GPU memory allocation..."
gpu_memory=$(vcgencmd get_mem gpu | cut -d'=' -f2 | tr -d 'M')
if [[ "$gpu_memory" -ge 64 ]]; then
    info "✓ GPU memory allocated: ${gpu_memory}MB"
else
    warning "GPU memory may be insufficient: ${gpu_memory}MB (recommended: 128MB)"
fi

log "✓ Display validation completed"

# ============================================================================
# Create Systemd Service for Boot Autostart
# ============================================================================
info "Creating systemd service for TSV6 boot autostart..."

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

sudo tee /etc/systemd/system/tsv6.service > /dev/null <<EOL
[Unit]
Description=TSV6 Application Service
After=graphical-session.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$WORK_DIR
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/$USER/.Xauthority
ExecStartPre=/bin/bash -c "sleep 15"
ExecStart=/bin/bash -c "source .venv/bin/activate && python3 main.py"
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=graphical.target
EOL

# Enable the service to start on boot
info "Enabling TSV6 service to start on boot..."
sudo systemctl daemon-reload
sudo systemctl enable tsv6.service

info "✓ Systemd service created and enabled for boot autostart"

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

# Create display diagnostic script
tee ~/display_diagnostics.sh > /dev/null <<'EOL'
#!/bin/bash
# TSV6 Display diagnostics script
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
echo "X11/LightDM status:"
systemctl status lightdm --no-pager -l
echo ""
echo "Display environment:"
echo "DISPLAY: ${DISPLAY:-Not set}"
echo "XAUTHORITY: ${XAUTHORITY:-Not set}"
echo ""
echo "OpenBox autostart:"
if [[ -f ~/.config/openbox/autostart ]]; then
    echo "✓ OpenBox autostart configured"
    cat ~/.config/openbox/autostart
else
    echo "❌ OpenBox autostart not found"
fi
EOL

chmod +x ~/display_diagnostics.sh

# Create advanced display troubleshooting script
tee ~/fix_display.sh > /dev/null <<'EOL'
#!/bin/bash
# Display troubleshooting and recovery script
echo "=== Display Troubleshooting ==="

# Function to fix common display issues
fix_display() {
    echo "Attempting to fix display issues..."

    # Restart display manager
    echo "Restarting LightDM display manager..."
    sudo systemctl restart lightdm

    # Wait for display manager to start
    sleep 5

    # Check if display is working
    if xrandr &>/dev/null; then
        echo "✓ Display restored successfully"
        xrandr --query
    else
        echo "⚠ Display still not responding"

        # Try alternative fix: restart X11
        echo "Restarting X11 server..."
        sudo systemctl restart display-manager

        sleep 5

        if xrandr &>/dev/null; then
            echo "✓ Display restored after X11 restart"
        else
            echo "❌ Display still not working - may need reboot"
        fi
    fi
}

# Check current display status
echo "Current display status:"
if [[ -e /dev/dri/card0 ]]; then
    echo "✓ DRM device: /dev/dri/card0"
else
    echo "❌ No DRM device found"
fi

if [[ -e /dev/fb0 ]]; then
    echo "✓ Framebuffer: /dev/fb0"
else
    echo "❌ No framebuffer found"
fi

# Check services
echo ""
echo "Service status:"
systemctl is-active lightdm && echo "✓ LightDM: Active" || echo "❌ LightDM: Inactive"
systemctl is-active graphical.target && echo "✓ Graphical target: Active" || echo "❌ Graphical target: Inactive"

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
echo "- sudo raspi-config (to reconfigure display settings)"
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
    ~/.config/openbox \
    ~/projects 2>/dev/null

echo "✓ Backup created: $BACKUP_DIR/tsv6_backup_$DATE.tar.gz"
echo "Backup size: $(du -h $BACKUP_DIR/tsv6_backup_$DATE.tar.gz | cut -f1)"
EOL

chmod +x ~/backup_tsv6.sh

log "✓ Maintenance scripts created successfully"

# ============================================================================
# FINAL SUMMARY AND NEXT STEPS
# ============================================================================
log "=========================================="
log "🎉 TSV6 Raspberry Pi Setup Complete!"
log "=========================================="

info "Summary of completed tasks:"
info "✓ System updated and TSV6-specific packages installed"
info "✓ Security hardening (firewall, fail2ban, SSH keys)"
info "✓ Python environment with UV package manager configured"
info "✓ Display system configured for Waveshare 7-inch DSI"
info "✓ Git and GitHub SSH keys generated"
if [[ -n "$GITHUB_REPO_URL" ]]; then
    info "✓ TSV6 project cloned and environment set up"
fi
if [[ "$CERTS_FOUND" == true ]]; then
    info "✓ AWS IoT certificates deployed to $CERTS_DIR"
fi
info "✓ Comprehensive diagnostics and maintenance scripts created"
info "✓ TSV6 systemd service enabled for automatic boot startup"

warning "CRITICAL NEXT STEPS:"
if [[ -n "$GITHUB_EMAIL" ]]; then
    warning "1. Add the GitHub SSH key (shown above) to your GitHub account"
    warning "2. Test GitHub connection: ssh -T git@github.com"
fi
warning "3. Configure your .env file with TSV6-specific settings"
warning "4. REBOOT THE SYSTEM: sudo reboot"
warning "   TSV6 will automatically start on boot - no login required!"
warning "   This is required for display configuration and SSH security"

info ""
info "After reboot, useful commands:"
info "• Display diagnostics: ~/display_diagnostics.sh"
info "• Display troubleshooting: ~/fix_display.sh"
info "• Control TSV6 app: ~/tsv6_control.sh {start|stop|restart|status|logs}"
info "• Backup system: ~/backup_tsv6.sh"
info "• TSV6 auto-starts on boot (no manual commands needed)"
info "• Control service: sudo systemctl {start|stop|restart|status} tsv6.service"

info ""
info "Important file locations:"
info "• Project: ~/projects/$REPO_NAME"
info "• Certificates: $CERTS_DIR (if deployed)"
info "• Config: /boot/firmware/config.txt"

info ""
info "Device information:"
info "• Hostname: $NEW_HOSTNAME"
info "• Thing Name: $THING_NAME"

log "TSV6 setup completed successfully! 🚀"
log "Remember to reboot: sudo reboot"
