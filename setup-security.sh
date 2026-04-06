#!/bin/bash
################################################################################
# TSV6 Security Hardening Script (Optional)
#
# Configures security measures for production deployment:
#   - UFW firewall (SSH, MQTT/AWS IoT ports)
#   - fail2ban for SSH brute force protection
#   - SSH hardening (disable password auth, root login)
#
# IMPORTANT: Ensure you have SSH key access before running this script!
#            Password authentication will be disabled.
#
# Run after: setup-dependencies.sh, setup-pi-config.sh, setup-services.sh
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

log "TSV6 Security Hardening"
echo "=================================="
echo ""
warning "This script will:"
warning "  - Enable UFW firewall"
warning "  - Configure fail2ban"
warning "  - Disable SSH password authentication"
warning "  - Disable SSH root login"
echo ""
warning "ENSURE YOU HAVE SSH KEY ACCESS BEFORE PROCEEDING!"
echo ""
if [ -t 0 ]; then
    # Interactive — prompt for confirmation
    read -p "Continue with security hardening? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Security hardening cancelled"
        exit 0
    fi
else
    # Non-interactive (piped input) — read from stdin
    read -r REPLY || REPLY=""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Security hardening cancelled (non-interactive, no 'y' received)"
        exit 0
    fi
    info "Non-interactive mode — proceeding with security hardening"
fi

# ============================================================================
# Install Security Packages
# ============================================================================
log "Installing security packages..."

sudo apt-get update
sudo apt-get install -y ufw fail2ban

success "Security packages installed"

# ============================================================================
# UFW Firewall Configuration
# ============================================================================
log "Configuring UFW firewall..."

# Reset to defaults
sudo ufw --force reset

# Set default policies
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH
sudo ufw allow ssh
info "Allowed SSH (port 22)"

# Allow MQTT over TLS for AWS IoT
sudo ufw allow 8883/tcp
info "Allowed MQTT/TLS (port 8883) for AWS IoT"

# Allow HTTPS for AWS IoT WebSocket
sudo ufw allow 443/tcp
info "Allowed HTTPS (port 443) for AWS IoT"

# Enable firewall
sudo ufw --force enable

success "UFW firewall enabled"

# ============================================================================
# fail2ban Configuration
# ============================================================================
log "Configuring fail2ban..."

# Backup existing config
if [ -f /etc/fail2ban/jail.local ]; then
    sudo cp /etc/fail2ban/jail.local /etc/fail2ban/jail.local.backup.$(date +%Y%m%d_%H%M%S)
fi

# Create fail2ban configuration
sudo tee /etc/fail2ban/jail.local > /dev/null << 'EOL'
[DEFAULT]
# Ban duration: 1 hour
bantime = 3600
# Detection window: 10 minutes
findtime = 600
# Max retries before ban
maxretry = 5
# Ignore localhost
ignoreip = 127.0.0.1/8 ::1

[sshd]
enabled = true
port = ssh
logpath = /var/log/auth.log
# Stricter for SSH: 3 attempts
maxretry = 3
# Longer ban for SSH: 24 hours
bantime = 86400
EOL

# Enable and restart fail2ban
sudo systemctl enable fail2ban
sudo systemctl restart fail2ban

success "fail2ban configured and enabled"

# ============================================================================
# SSH Hardening
# ============================================================================
log "Hardening SSH configuration..."

# Backup existing config
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup.$(date +%Y%m%d_%H%M%S)

# Disable password authentication
sudo sed -i 's/^#PasswordAuthentication yes/PasswordAuthentication no/g' /etc/ssh/sshd_config
sudo sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/g' /etc/ssh/sshd_config

# Disable root login
sudo sed -i 's/^#PermitRootLogin prohibit-password/PermitRootLogin no/g' /etc/ssh/sshd_config
sudo sed -i 's/^PermitRootLogin yes/PermitRootLogin no/g' /etc/ssh/sshd_config
sudo sed -i 's/^PermitRootLogin prohibit-password/PermitRootLogin no/g' /etc/ssh/sshd_config

# Enable public key authentication (should already be enabled)
sudo sed -i 's/^#PubkeyAuthentication yes/PubkeyAuthentication yes/g' /etc/ssh/sshd_config

# Restart SSH
sudo systemctl restart ssh

success "SSH hardened"

# ============================================================================
# Validation
# ============================================================================
log "Validating security configuration..."

echo ""
info "UFW Status:"
sudo ufw status verbose

echo ""
info "fail2ban Status:"
sudo systemctl status fail2ban --no-pager -l | head -20

echo ""
info "SSH Configuration:"
echo "  PasswordAuthentication: $(grep -E "^PasswordAuthentication" /etc/ssh/sshd_config || echo 'not set')"
echo "  PermitRootLogin: $(grep -E "^PermitRootLogin" /etc/ssh/sshd_config || echo 'not set')"
echo "  PubkeyAuthentication: $(grep -E "^PubkeyAuthentication" /etc/ssh/sshd_config || echo 'not set (default: yes)')"

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "=================================="
log "Security Hardening Summary"
echo "=================================="
echo ""
info "Firewall (UFW):"
echo "  - Default: deny incoming, allow outgoing"
echo "  - Allowed: SSH (22), MQTT/TLS (8883), HTTPS (443)"
echo ""
info "fail2ban:"
echo "  - SSH: 3 attempts, 24-hour ban"
echo "  - General: 5 attempts, 1-hour ban"
echo ""
info "SSH:"
echo "  - Password authentication: DISABLED"
echo "  - Root login: DISABLED"
echo "  - Public key authentication: ENABLED"
echo ""
warning "IMPORTANT: Test SSH key access in a NEW terminal before closing this session!"
warning "           If locked out, you'll need physical console access."
echo ""
info "Useful commands:"
echo "  - Check firewall: sudo ufw status"
echo "  - Check banned IPs: sudo fail2ban-client status sshd"
echo "  - Unban IP: sudo fail2ban-client set sshd unbanip <IP>"
echo ""

exit 0
