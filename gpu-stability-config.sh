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

configure_gpu_memory() {
    log "Configuring GPU memory allocation..."
    backup_file "/boot/config.txt"
    
    # Increase GPU memory to 128MB for stable video processing
    if ! grep -q "gpu_mem=128" /boot/config.txt; then
        echo "gpu_mem=128" >> /boot/config.txt
        log "Set GPU memory to 128MB"
    fi
    
    # Disable GPU memory split dynamic allocation to prevent instability
    if ! grep -q "gpu_mem_256=128" /boot/config.txt; then
        echo "gpu_mem_256=128" >> /boot/config.txt
        echo "gpu_mem_512=128" >> /boot/config.txt
        echo "gpu_mem_1024=128" >> /boot/config.txt
        log "Set fixed GPU memory allocation for all RAM sizes"
    fi
}

configure_vc4_stability() {
    log "Configuring VC4 driver stability settings..."
    
    # Disable problematic VC4 features that cause atomic commit issues
    if ! grep -q "dtoverlay=vc4-kms-v3d,cma-256" /boot/config.txt; then
        echo "dtoverlay=vc4-kms-v3d,cma-256" >> /boot/config.txt
        log "Configured VC4 with increased CMA allocation"
    fi
    
    # Keep DSI active and enable HDMI for an external portable monitor.
    sed -i '/^hdmi_ignore_hotplug=/d' /boot/config.txt
    sed -i '/^hdmi_ignore_composite=/d' /boot/config.txt
    sed -i '/^hdmi_blanking=/d' /boot/config.txt

    if ! grep -q "hdmi_force_hotplug=1" /boot/config.txt; then
        echo "hdmi_force_hotplug=1" >> /boot/config.txt
        echo "hdmi_group=2" >> /boot/config.txt
        echo "hdmi_mode=82" >> /boot/config.txt  # 1920x1080 60Hz
        echo "hdmi_drive=2" >> /boot/config.txt
        log "Enabled HDMI output for external portable monitor"
    fi

    # Keep display auto-detection on so HDMI hotplug works alongside explicit DSI.
    sed -i '/^display_auto_detect=/d' /boot/config.txt
    echo "display_auto_detect=1" >> /boot/config.txt
    log "Enabled display auto-detection for DSI + HDMI"
    
    # Disable power management features that can cause GPU instability
    if ! grep -q "avoid_warnings=1" /boot/config.txt; then
        echo "avoid_warnings=1" >> /boot/config.txt
        echo "disable_overscan=1" >> /boot/config.txt
        echo "max_usb_current=1" >> /boot/config.txt
        log "Disabled power management warnings and overscan"
    fi
}

configure_kernel_parameters() {
    log "Configuring kernel parameters for video stability..."
    backup_file "/boot/cmdline.txt"
    
    # Add kernel parameters to improve video stability
    local cmdline=$(cat /boot/cmdline.txt)
    local new_params="cma=256M@256M"
    
    if [[ ! "$cmdline" =~ cma=256M ]]; then
        echo "$cmdline $new_params" > /boot/cmdline.txt
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
