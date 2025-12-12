#!/bin/bash
# GPU Monitoring and Recovery Script
# Monitors GPU health and automatically recovers from VC4 driver issues

set -euo pipefail

LOG_FILE="/var/log/gpu-monitor.log"
ERROR_THRESHOLD=5
ERROR_COUNT=0
MONITOR_INTERVAL=30
RECOVERY_COOLDOWN=300  # 5 minutes between recovery attempts
LAST_RECOVERY=0

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [GPU-MONITOR] $1" | tee -a "$LOG_FILE"
}

check_gpu_health() {
    local errors=0
    
    # Check for VC4 atomic commit errors in dmesg
    if dmesg | tail -100 | grep -q "vc4_atomic_commit_tail"; then
        ((errors++))
        log "WARNING: VC4 atomic commit errors detected in kernel log"
    fi
    
    # Check GPU temperature
    local temp=$(vcgencmd measure_temp | sed 's/temp=//;s/'\''C//')
    if (( $(echo "$temp > 70.0" | bc -l) )); then
        ((errors++))
        log "WARNING: GPU temperature high: ${temp}°C"
    fi
    
    # Check for throttling
    local throttled=$(vcgencmd get_throttled)
    if [[ "$throttled" != "throttled=0x0" ]]; then
        ((errors++))
        log "WARNING: System throttling detected: $throttled"
    fi
    
    # Check DSI display status
    if [[ -e /sys/class/drm/card1-DSI-1/status ]]; then
        local dsi_status=$(cat /sys/class/drm/card1-DSI-1/status)
        if [[ "$dsi_status" != "connected" ]]; then
            ((errors++))
            log "WARNING: DSI display connection issue detected"
        fi
    fi
    
    return $errors
}

recover_gpu() {
    local current_time=$(date +%s)
    
    # Prevent frequent recovery attempts
    if (( current_time - LAST_RECOVERY < RECOVERY_COOLDOWN )); then
        log "Recovery cooldown active, skipping recovery attempt"
        return
    fi
    
    log "Attempting GPU recovery..."
    LAST_RECOVERY=$current_time
    
    # Clear kernel message buffer
    dmesg -c > /dev/null 2>&1 || true
    
    # Reset display if possible
    if command -v xset >/dev/null 2>&1; then
        DISPLAY=:0 xset dpms force off
        sleep 2
        DISPLAY=:0 xset dpms force on
        log "Reset display power management"
    fi
    
    # Force GPU memory cleanup
    echo 3 > /proc/sys/vm/drop_caches
    log "Cleared system caches"
    
    # Reset error counter after recovery attempt
    ERROR_COUNT=0
    log "GPU recovery attempt completed"
}

restart_video_processes() {
    log "Restarting video-related processes..."
    
    # Find and restart video processes (adjust based on your video player)
    local video_procs=$(pgrep -f "(vlc|mpv|omxplayer|ffmpeg|gstreamer)" || true)
    
    if [[ -n "$video_procs" ]]; then
        log "Found video processes: $video_procs"
        # Graceful termination first
        pkill -TERM -f "(vlc|mpv|omxplayer|ffmpeg|gstreamer)" || true
        sleep 5
        # Force kill if still running
        pkill -KILL -f "(vlc|mpv|omxplayer|ffmpeg|gstreamer)" || true
        log "Video processes terminated"
        
        # Restart video service if it exists
        if systemctl is-active --quiet tsv6.service; then
            systemctl restart tsv6.service
            log "Restarted tsv6 service"
        fi
    else
        log "No video processes found to restart"
    fi
}

send_alert() {
    local message="$1"
    log "ALERT: $message"
    
    # Log to system journal
    logger -p daemon.crit "GPU-MONITOR: $message"
    
    # Optional: Send to external monitoring system
    # curl -X POST "http://monitoring-endpoint" -d "alert=$message" || true
}

monitor_loop() {
    log "Starting GPU monitoring loop (interval: ${MONITOR_INTERVAL}s)"
    
    while true; do
        if check_gpu_health; then
            ((ERROR_COUNT++))
            log "GPU health check failed (error count: $ERROR_COUNT)"
            
            if (( ERROR_COUNT >= ERROR_THRESHOLD )); then
                send_alert "GPU stability issues detected, initiating recovery"
                recover_gpu
                
                # If errors persist, restart video processes
                sleep 10
                if check_gpu_health; then
                    restart_video_processes
                fi
            fi
        else
            if (( ERROR_COUNT > 0 )); then
                log "GPU health check passed, resetting error count"
                ERROR_COUNT=0
            fi
        fi
        
        sleep $MONITOR_INTERVAL
    done
}

main() {
    log "GPU Monitor starting up..."
    
    # Ensure we have necessary tools
    if ! command -v vcgencmd >/dev/null 2>&1; then
        log "ERROR: vcgencmd not available"
        exit 1
    fi
    
    if ! command -v bc >/dev/null 2>&1; then
        log "Installing bc for temperature calculations..."
        apt-get update && apt-get install -y bc
    fi
    
    # Create log directory if it doesn't exist
    mkdir -p "$(dirname "$LOG_FILE")"
    
    # Start monitoring
    monitor_loop
}

# Handle script termination gracefully
trap 'log "GPU Monitor shutting down..."; exit 0' TERM INT

main "$@"
