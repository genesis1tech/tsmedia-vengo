#!/bin/bash
#
# Swap Optimization Script for TSV6 Raspberry Pi Systems
# Increases swap size, optimizes configuration, and improves performance
#

set -euo pipefail

# Configuration
SWAP_FILE="/var/swap"
NEW_SWAP_SIZE_GB=2
BACKUP_DIR="/tmp/swap_backup_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/tmp/swap_optimization.log"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Error handling
error_exit() {
    log "ERROR: $1"
    exit 1
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        error_exit "This script must be run as root (use sudo)"
    fi
}

# Create backup directory
create_backup_dir() {
    mkdir -p "$BACKUP_DIR"
    log "Created backup directory: $BACKUP_DIR"
}

# Backup current swap configuration
backup_swap_config() {
    log "Backing up current swap configuration..."
    
    # Backup fstab
    cp /etc/fstab "$BACKUP_DIR/fstab.backup"
    
    # Save current swap info
    swapon --show > "$BACKUP_DIR/current_swap_info.txt"
    cat /proc/swaps > "$BACKUP_DIR/current_proc_swaps.txt"
    cat /proc/sys/vm/swappiness > "$BACKUP_DIR/current_swappiness.txt"
    
    # Save current memory info
    free -h > "$BACKUP_DIR/current_memory_info.txt"
    
    log "Backup completed in: $BACKUP_DIR"
}

# Check current swap status
check_current_swap() {
    log "Checking current swap configuration..."
    
    if [[ -f "$SWAP_FILE" ]]; then
        CURRENT_SIZE=$(ls -lh "$SWAP_FILE" | awk '{print $5}')
        log "Current swap file: $SWAP_FILE ($CURRENT_SIZE)"
    else
        log "No existing swap file found at $SWAP_FILE"
    fi
    
    # Check if swap is active
    if swapon --show | grep -q "$SWAP_FILE"; then
        log "Swap is currently active"
        SWAP_ACTIVE=1
    else
        log "No active swap found"
        SWAP_ACTIVE=0
    fi
    
    # Check current swappiness
    CURRENT_SWAPPINESS=$(cat /proc/sys/vm/swappiness)
    log "Current swappiness: $CURRENT_SWAPPINESS"
}

# Disable current swap
disable_current_swap() {
    if [[ $SWAP_ACTIVE -eq 1 ]]; then
        log "Disabling current swap..."
        swapoff "$SWAP_FILE" || log "Warning: Could not disable swap (may not exist)"
        log "Swap disabled"
    fi
}

# Create new swap file
create_new_swap() {
    log "Creating new swap file (${NEW_SWAP_SIZE_GB}GB)..."
    
    # Remove old swap file if it exists
    if [[ -f "$SWAP_FILE" ]]; then
        log "Removing old swap file..."
        rm -f "$SWAP_FILE"
    fi
    
    # Create new swap file with better performance settings
    log "Allocating ${NEW_SWAP_SIZE_GB}GB swap file..."
    
    # Use fallocate if available (faster), fallback to dd
    if command -v fallocate &> /dev/null; then
        fallocate -l "${NEW_SWAP_SIZE_GB}G" "$SWAP_FILE"
        log "Swap file created using fallocate"
    else
        dd if=/dev/zero of="$SWAP_FILE" bs=1M count=$((NEW_SWAP_SIZE_GB * 1024)) status=progress
        log "Swap file created using dd"
    fi
    
    # Set proper permissions
    chmod 600 "$SWAP_FILE"
    chown root:root "$SWAP_FILE"
    
    # Make it a swap file
    mkswap "$SWAP_FILE"
    log "Swap file formatted"
}

# Enable new swap
enable_new_swap() {
    log "Enabling new swap file..."
    swapon "$SWAP_FILE"
    log "New swap file enabled"
    
    # Verify swap is working
    NEW_SWAP_INFO=$(swapon --show)
    log "New swap status: $NEW_SWAP_INFO"
}

# Update fstab for persistent swap
update_fstab() {
    log "Updating /etc/fstab for persistent swap..."
    
    # Remove existing swap entries for our file
    grep -v "$SWAP_FILE" /etc/fstab > /tmp/fstab.tmp || true
    
    # Add new swap entry
    echo "$SWAP_FILE    none    swap    sw    0    0" >> /tmp/fstab.tmp
    
    # Replace fstab
    mv /tmp/fstab.tmp /etc/fstab
    log "Updated /etc/fstab"
}

# Optimize swap settings
optimize_swap_settings() {
    log "Optimizing swap settings..."
    
    # Set optimal swappiness for Pi with limited RAM
    # Lower value = less aggressive swapping, but we need some swapping
    OPTIMAL_SWAPPINESS=40
    
    echo "$OPTIMAL_SWAPPINESS" > /proc/sys/vm/swappiness
    log "Set swappiness to $OPTIMAL_SWAPPINESS"
    
    # Make swappiness setting persistent
    if grep -q "vm.swappiness" /etc/sysctl.conf; then
        sed -i "s/vm.swappiness.*/vm.swappiness = $OPTIMAL_SWAPPINESS/" /etc/sysctl.conf
    else
        echo "vm.swappiness = $OPTIMAL_SWAPPINESS" >> /etc/sysctl.conf
    fi
    
    # Optimize other VM settings for better performance
    cat >> /etc/sysctl.conf << 'SYSCTL_EOF'

# Swap optimization settings added by TSV6 memory optimizer
vm.vfs_cache_pressure = 50
vm.dirty_background_ratio = 5
vm.dirty_ratio = 10
SYSCTL_EOF
    
    log "Added persistent swap optimization settings to /etc/sysctl.conf"
}

# Verify swap optimization
verify_optimization() {
    log "Verifying swap optimization..."
    
    # Check swap size
    SWAP_SIZE=$(swapon --show | grep "$SWAP_FILE" | awk '{print $3}')
    log "New swap size: $SWAP_SIZE"
    
    # Check memory situation
    MEMORY_INFO=$(free -h)
    log "Current memory status:"
    echo "$MEMORY_INFO" | tee -a "$LOG_FILE"
    
    # Check swappiness
    CURRENT_SWAPPINESS=$(cat /proc/sys/vm/swappiness)
    log "Current swappiness: $CURRENT_SWAPPINESS"
}

# Main optimization function
main() {
    log "Starting TSV6 swap optimization..."
    log "Target swap size: ${NEW_SWAP_SIZE_GB}GB"
    
    check_root
    create_backup_dir
    backup_swap_config
    check_current_swap
    disable_current_swap
    create_new_swap
    enable_new_swap
    update_fstab
    optimize_swap_settings
    verify_optimization
    
    log "Swap optimization completed successfully!"
    log "Backup files saved in: $BACKUP_DIR"
    log "Log file: $LOG_FILE"
    
    echo ""
    echo "=== SWAP OPTIMIZATION SUMMARY ==="
    echo "✅ Swap file increased to ${NEW_SWAP_SIZE_GB}GB"
    echo "✅ Swappiness optimized for Pi hardware"
    echo "✅ Persistent configuration updated"
    echo "✅ Performance settings optimized"
    echo ""
    echo "Next steps:"
    echo "1. Reboot to ensure all settings are persistent"
    echo "2. Monitor memory usage with: python3 scripts/system_monitoring/memory_monitor.py --once"
    echo ""
    echo "Backup location: $BACKUP_DIR"
}

# Run main function
main "$@"