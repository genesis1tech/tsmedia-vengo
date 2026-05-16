#!/bin/bash
# GPU Stability Configuration for Production Video Playback
# Addresses VC4 driver issues and ensures 24/7 reliability

set -euo pipefail

LOG_FILE="/var/log/gpu-stability-setup.log"
BACKUP_DIR="/etc/gpu-stability-backups"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

backup_file() {
    local file="$1"
    if [[ -f "$file" ]]; then
        mkdir -p "$BACKUP_DIR"
        cp "$file" "$BACKUP_DIR/$(basename "$file").backup.$(date +%s)"
        log "Backed up $file"
    fi
}

resolve_file() {
    local file="$1"
    if command -v readlink >/dev/null 2>&1; then
        readlink -f "$file"
    else
        echo "$file"
    fi
}

get_boot_config_file() {
    if [[ -f /boot/firmware/config.txt ]]; then
        resolve_file /boot/firmware/config.txt
    elif [[ -f /boot/config.txt ]]; then
        resolve_file /boot/config.txt
    else
        echo "Could not find Raspberry Pi boot config file" >&2
        exit 1
    fi
}

get_boot_cmdline_file() {
    if [[ -f /boot/firmware/cmdline.txt ]]; then
        resolve_file /boot/firmware/cmdline.txt
    elif [[ -f /boot/cmdline.txt ]]; then
        resolve_file /boot/cmdline.txt
    else
        echo "Could not find Raspberry Pi boot cmdline file" >&2
        exit 1
    fi
}

configure_gpu_memory() {
    log "Skipping direct boot config GPU memory edits; managed by scripts/install-boot-config.sh"
}

configure_vc4_stability() {
    log "Skipping direct boot config VC4/display edits; managed by scripts/install-boot-config.sh"
}

configure_kernel_parameters() {
    log "Configuring kernel parameters for video stability..."
    local cmdline_file
    cmdline_file="$(get_boot_cmdline_file)"
    backup_file "$cmdline_file"
    
    # Add kernel parameters to improve video stability
    local cmdline
    cmdline=$(cat "$cmdline_file")
    local new_params="cma=256M@256M"
    
    if [[ ! "$cmdline" =~ cma=256M ]]; then
        echo "$cmdline $new_params" > "$cmdline_file"
        log "Added kernel parameters for stable video output"
    fi
}

configure_system_limits() {
    log "Configuring system limits for video processes..."
    
    # Create limits configuration for video processes
    cat > /etc/security/limits.d/99-video-stability.conf << 'LIMITS_EOF'
# Video process limits for stability
*               soft    nofile          65536
*               hard    nofile          65536
*               soft    memlock         unlimited
*               hard    memlock         unlimited
root            soft    nofile          65536
root            hard    nofile          65536
LIMITS_EOF
    
    log "Configured system limits for video processes"
}

create_gpu_monitoring_service() {
    log "Creating GPU monitoring service..."
    
    cat > /etc/systemd/system/gpu-monitor.service << 'SERVICE_EOF'
[Unit]
Description=GPU Stability Monitor
After=graphical-session.target
Wants=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/local/bin/gpu-monitor.sh
Restart=always
RestartSec=10
User=root
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=gpu-monitor

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    systemctl daemon-reload
    systemctl enable gpu-monitor.service
    log "Created and enabled GPU monitoring service"
}

main() {
    if [[ $EUID -ne 0 ]]; then
        echo "This script must be run as root"
        exit 1
    fi
    
    log "Starting GPU stability configuration..."
    
    configure_gpu_memory
    configure_vc4_stability
    configure_kernel_parameters
    configure_system_limits
    create_gpu_monitoring_service
    
    log "GPU stability configuration completed. Reboot required."
    echo "Configuration complete. Please reboot the system to apply changes."
}

main "$@"
