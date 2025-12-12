#!/bin/bash

# Install pigpio for servo control on GPIO18
# This script adds pigpio support for TSV6 servo control

set -e

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warning() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

info() {
    echo -e "${BLUE}[INFO] $1${NC}"
}

log "Installing pigpio for GPIO servo control..."

# Install pigpio system package
info "Installing pigpio system packages..."
sudo apt update
sudo apt install -y pigpio python3-pigpio

# Enable and start pigpio daemon
info "Configuring pigpio daemon..."
sudo systemctl enable pigpiod
sudo systemctl start pigpiod

# Check if daemon is running
if systemctl is-active --quiet pigpiod; then
    info "✓ pigpiod service is running"
else
    warning "pigpiod service failed to start"
    sudo systemctl status pigpiod
fi

# Install Python pigpio library via UV
info "Installing Python pigpio library with UV..."
if command -v uv >/dev/null 2>&1; then
    # Check if we're in a virtual environment
    if [[ "$VIRTUAL_ENV" != "" ]] || [[ -f ".venv/bin/activate" ]]; then
        if [[ "$VIRTUAL_ENV" == "" ]]; then
            source .venv/bin/activate
        fi
        uv pip install pigpio
    else
        # Install globally with UV
        uv tool install pigpio
    fi
    info "✓ pigpio Python library installed"
else
    warning "UV not found, installing pigpio with pip..."
    pip3 install pigpio
fi

# Test pigpio installation
info "Testing pigpio installation..."
if python3 -c "import pigpio; print('pigpio version:', pigpio.VERSION)" 2>/dev/null; then
    info "✓ pigpio Python library test successful"
else
    warning "pigpio Python library test failed"
fi

# Display GPIO18 servo information
info "GPIO18 servo configuration:"
echo "  - GPIO Pin: 18 (Physical pin 12)"
echo "  - PWM Frequency: 50Hz (20ms period)"
echo "  - Servo Control: 1ms-2ms pulse width"
echo "  - pigpiod daemon: $(systemctl is-active pigpiod)"

log "✓ pigpio setup complete for GPIO18 servo control"

