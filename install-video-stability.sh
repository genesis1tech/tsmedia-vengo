#!/bin/bash
# Production Video Stability Installation Script
# Installs all components for 24/7 reliable video playback

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/video-stability-install.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "This script must be run as root. Use: sudo $0"
        exit 1
    fi
}

install_dependencies() {
    log "Installing required dependencies..."
    
    apt-get update
    apt-get install -y \
        bc \
        curl \
        psmisc \
        x11-xserver-utils \
        htop \
        iotop \
        dstat
    
    log "Dependencies installed successfully"
}

install_scripts() {
    log "Installing monitoring scripts..."
    
    # Copy scripts to system locations
    cp "$SCRIPT_DIR/gpu-monitor.sh" /usr/local/bin/
    cp "$SCRIPT_DIR/video-watchdog.sh" /usr/local/bin/
    cp "$SCRIPT_DIR/gpu-stability-config.sh" /usr/local/bin/
    
    # Set permissions
    chmod +x /usr/local/bin/gpu-monitor.sh
    chmod +x /usr/local/bin/video-watchdog.sh
    chmod +x /usr/local/bin/gpu-stability-config.sh
    
    log "Scripts installed to /usr/local/bin/"
}

install_services() {
    log "Installing systemd services..."
    
    # Create GPU monitor service
    cat > /etc/systemd/system/gpu-monitor.service << 'SERVICE_EOF'
[Unit]
Description=GPU Stability Monitor
After=graphical.target
Wants=graphical.target

[Service]
Type=simple
ExecStart=/usr/local/bin/gpu-monitor.sh
Restart=always
RestartSec=30
User=root
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gpu-monitor

# Resource limits
MemoryLimit=64M
CPUQuota=5%

# Security settings
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/log /proc/sys/vm

[Install]
WantedBy=multi-user.target
SERVICE_EOF
    
    # Install video watchdog service
    cp "$SCRIPT_DIR/video-watchdog.service" /etc/systemd/system/
    
    # Reload systemd and enable services
    systemctl daemon-reload
    systemctl enable gpu-monitor.service
    systemctl enable video-watchdog.service
    
    log "Systemd services installed and enabled"
}

configure_log_rotation() {
    log "Configuring log rotation..."
    
    cat > /etc/logrotate.d/video-stability << 'LOGROTATE_EOF'
/var/log/gpu-monitor.log
/var/log/video-watchdog.log
/var/log/gpu-stability-setup.log {
    daily
    missingok
    rotate 7
    compress
    notifempty
    create 644 root root
    postrotate
        systemctl reload-or-restart rsyslog > /dev/null 2>&1 || true
    endscript
}
LOGROTATE_EOF
    
    log "Log rotation configured"
}

create_health_check_script() {
    log "Creating system health check script..."
    
    cat > /usr/local/bin/video-health-check.sh << 'HEALTH_EOF'
#!/bin/bash
# Video System Health Check
# Quick diagnostic script for troubleshooting

set -euo pipefail

echo "=== Video System Health Check ==="
echo "Date: $(date)"
echo

echo "=== GPU Status ==="
vcgencmd measure_temp
vcgencmd get_throttled
vcgencmd get_mem gpu
vcgencmd get_mem arm
echo

echo "=== Video Services Status ==="
systemctl status tsv6.service --no-pager -l
echo
systemctl status gpu-monitor.service --no-pager -l
echo
systemctl status video-watchdog.service --no-pager -l
echo

echo "=== Video Processes ==="
pgrep -fl "python.*tsv6\|vlc\|mpv\|omxplayer" || echo "No video processes found"
echo

echo "=== Display Status ==="
if command -v xrandr >/dev/null 2>&1; then
    DISPLAY=:0 xrandr | grep -E "(connected|disconnected)"
else
    echo "xrandr not available"
fi
echo

echo "=== Recent GPU Errors (last 50 lines) ==="
dmesg | tail -50 | grep -i "vc4\|gpu\|drm" || echo "No recent GPU errors"
echo

echo "=== System Resources ==="
free -h
echo
df -h /
echo

echo "=== Recent Log Entries ==="
echo "GPU Monitor (last 5):"
tail -5 /var/log/gpu-monitor.log 2>/dev/null || echo "No GPU monitor logs"
echo
echo "Video Watchdog (last 5):"
tail -5 /var/log/video-watchdog.log 2>/dev/null || echo "No watchdog logs"
echo

echo "=== Health Check Complete ==="
HEALTH_EOF
    
    chmod +x /usr/local/bin/video-health-check.sh
    log "Health check script created at /usr/local/bin/video-health-check.sh"
}

run_gpu_stability_config() {
    log "Running GPU stability configuration..."
    
    if [[ -f "$SCRIPT_DIR/gpu-stability-config.sh" ]]; then
        "$SCRIPT_DIR/gpu-stability-config.sh"
    else
        /usr/local/bin/gpu-stability-config.sh
    fi
}

show_completion_message() {
    log "Installation completed successfully!"
    
    cat << 'COMPLETION_EOF'

╔══════════════════════════════════════════════════════════════════╗
║                 Video Stability Installation Complete!           ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  ✓ GPU stability configuration applied                           ║
║  ✓ GPU monitoring service installed and enabled                  ║
║  ✓ Video watchdog service installed and enabled                  ║
║  ✓ Log rotation configured                                       ║
║  ✓ Health check script created                                   ║
║                                                                  ║
║  IMPORTANT: A system reboot is required to apply all changes!    ║
║                                                                  ║
║  After reboot, check services with:                              ║
║    sudo systemctl status gpu-monitor video-watchdog             ║
║                                                                  ║
║  Run health check anytime with:                                  ║
║    sudo /usr/local/bin/video-health-check.sh                    ║
║                                                                  ║
║  Logs are available at:                                          ║
║    /var/log/gpu-monitor.log                                      ║
║    /var/log/video-watchdog.log                                   ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝

COMPLETION_EOF
}

main() {
    log "Starting video stability installation..."
    
    check_root
    install_dependencies
    install_scripts
    install_services
    configure_log_rotation
    create_health_check_script
    run_gpu_stability_config
    
    show_completion_message
    
    log "Installation process completed. Reboot required."
}

main "$@"
