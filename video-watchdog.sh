#!/bin/bash
# Video Watchdog Service
# Monitors video playback processes and ensures continuous operation

set -euo pipefail

LOG_FILE="/var/log/video-watchdog.log"
VIDEO_SERVICE="tsv6.service"
CHECK_INTERVAL=15
MAX_RESTART_ATTEMPTS=3
RESTART_WINDOW=300  # 5 minutes
RESTART_COUNT=0
WINDOW_START=0

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [VIDEO-WATCHDOG] $1" | tee -a "$LOG_FILE"
}

check_video_processes() {
    # Check if video service is running
    if ! systemctl is-active --quiet "$VIDEO_SERVICE"; then
        log "WARNING: $VIDEO_SERVICE is not active"
        return 1
    fi
    
    # Check for video-related processes
    local video_procs=$(pgrep -f "(python.*tsv6|vlc|mpv|omxplayer)" 2>/dev/null || true)
    if [[ -z "$video_procs" ]]; then
        log "WARNING: No video processes found"
        return 1
    fi
    
    # Check for zombie processes
    local zombies=$(ps aux | grep -E "(python.*tsv6|vlc|mpv)" | grep -c "<defunct>" || echo "0")
    if (( zombies > 0 )); then
        log "WARNING: $zombies zombie video processes detected"
        return 1
    fi
    
    # Check DSI display output
    if [[ -e /sys/class/drm/card1-DSI-1/enabled ]]; then
        local dsi_enabled=$(cat /sys/class/drm/card1-DSI-1/enabled)
        if [[ "$dsi_enabled" != "enabled" ]]; then
            log "WARNING: DSI display not enabled"
            return 1
        fi
    fi
    fi
    
    return 0
}

reset_restart_counter() {
    local current_time=$(date +%s)
    if (( current_time - WINDOW_START > RESTART_WINDOW )); then
        RESTART_COUNT=0
        WINDOW_START=$current_time
        log "Reset restart counter after time window"
    fi
}

restart_video_service() {
    local current_time=$(date +%s)
    
    # Initialize window if needed
    if (( WINDOW_START == 0 )); then
        WINDOW_START=$current_time
    fi
    
    reset_restart_counter
    
    if (( RESTART_COUNT >= MAX_RESTART_ATTEMPTS )); then
        log "ERROR: Maximum restart attempts ($MAX_RESTART_ATTEMPTS) reached in time window"
        log "Sending critical alert and waiting for manual intervention"
        logger -p daemon.crit "VIDEO-WATCHDOG: Service restart limit exceeded, manual intervention required"
        sleep 600  # Wait 10 minutes before next attempt
        RESTART_COUNT=0
        WINDOW_START=$(date +%s)
        return 1
    fi
    
    ((RESTART_COUNT++))
    log "Attempting to restart $VIDEO_SERVICE (attempt $RESTART_COUNT/$MAX_RESTART_ATTEMPTS)"
    
    # Stop service gracefully
    if systemctl is-active --quiet "$VIDEO_SERVICE"; then
        systemctl stop "$VIDEO_SERVICE"
        sleep 3
    fi
    
    # Clean up any remaining processes
    pkill -f "python.*tsv6" || true
    sleep 2
    
    # Clear any GPU-related issues
    echo 3 > /proc/sys/vm/drop_caches
    
    # Restart service
    systemctl start "$VIDEO_SERVICE"
    sleep 5
    
    if systemctl is-active --quiet "$VIDEO_SERVICE"; then
        log "Successfully restarted $VIDEO_SERVICE"
        return 0
    else
        log "ERROR: Failed to restart $VIDEO_SERVICE"
        return 1
    fi
}

perform_health_check() {
    # Check system resources
    local mem_usage=$(free | grep Mem | awk '{printf "%.1f", $3/$2 * 100.0}')
    local cpu_usage=$(top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{printf "%.1f", 100 - $1}')
    local disk_usage=$(df / | tail -1 | awk '{printf "%.1f", $5}' | sed 's/%//')
    
    log "System resources - CPU: ${cpu_usage}%, Memory: ${mem_usage}%, Disk: ${disk_usage}%"
    
    # Alert on high resource usage
    if (( $(echo "$mem_usage > 90.0" | bc -l) )); then
        log "WARNING: High memory usage: ${mem_usage}%"
    fi
    
    if (( $(echo "$cpu_usage > 95.0" | bc -l) )); then
        log "WARNING: High CPU usage: ${cpu_usage}%"
    fi
    
    if (( $(echo "$disk_usage > 90.0" | bc -l) )); then
        log "WARNING: High disk usage: ${disk_usage}%"
    fi
}

monitor_loop() {
    log "Starting video watchdog monitoring (interval: ${CHECK_INTERVAL}s)"
    
    while true; do
        if ! check_video_processes; then
            log "Video process check failed, attempting restart"
            if ! restart_video_service; then
                log "Failed to restart video service, will retry next cycle"
            fi
        else
            # Periodic health check
            perform_health_check
        fi
        
        sleep $CHECK_INTERVAL
    done
}

main() {
    log "Video Watchdog starting up..."
    
    # Ensure bc is available for calculations
    if ! command -v bc >/dev/null 2>&1; then
        apt-get update && apt-get install -y bc
    fi
    
    # Create log directory
    mkdir -p "$(dirname "$LOG_FILE")"
    
    # Start monitoring
    monitor_loop
}

# Handle script termination
trap 'log "Video Watchdog shutting down..."; exit 0' TERM INT

main "$@"
