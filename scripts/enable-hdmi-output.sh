#!/bin/bash
# Enable HDMI output alongside the existing Waveshare DSI display.
#
# This keeps the current DSI configuration intact. It removes old DSI-only HDMI
# disable flags, adds a small TSV6 HDMI boot block, and tries to turn on a
# connected HDMI monitor in the running X11 session.

set -euo pipefail

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }
warn() { echo "[WARNING] $*" >&2; }

if [ -f /boot/firmware/config.txt ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    CONFIG_FILE="/boot/config.txt"
else
    echo "Could not find /boot/firmware/config.txt or /boot/config.txt" >&2
    exit 1
fi

if [ "$EUID" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

log "Using boot config: $CONFIG_FILE"
$SUDO cp "$CONFIG_FILE" "$CONFIG_FILE.backup.$(date +%Y%m%d_%H%M%S)"

log "Removing DSI-only HDMI disable flags"
$SUDO sed -i '/^hdmi_ignore_hotplug=/d' "$CONFIG_FILE"
$SUDO sed -i '/^hdmi_ignore_composite=/d' "$CONFIG_FILE"
$SUDO sed -i '/^hdmi_blanking=/d' "$CONFIG_FILE"
$SUDO sed -i '/^display_auto_detect=/d' "$CONFIG_FILE"

log "Refreshing TSV6 HDMI enable block"
$SUDO sed -i '/# BEGIN TSV6 HDMI Output Configuration/,/# END TSV6 HDMI Output Configuration/d' "$CONFIG_FILE"

$SUDO tee -a "$CONFIG_FILE" > /dev/null <<'EOL'

# BEGIN TSV6 HDMI Output Configuration
# ====================================================================
# TSV6 HDMI Output Configuration
# Enables an external portable monitor alongside the existing DSI display.
# DSI overlay, framebuffer, and portrait-mode settings are intentionally
# left unchanged.
# ====================================================================
display_auto_detect=1
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=82
hdmi_drive=2
max_framebuffers=2
# END TSV6 HDMI Output Configuration

EOL

log "Boot HDMI settings applied"

if command -v xrandr >/dev/null 2>&1; then
    export DISPLAY="${DISPLAY:-:0}"
    if [ -z "${XAUTHORITY:-}" ]; then
        if [ -f "$HOME/.Xauthority" ]; then
            export XAUTHORITY="$HOME/.Xauthority"
        elif [ -n "${SUDO_USER:-}" ] && [ -f "/home/$SUDO_USER/.Xauthority" ]; then
            export XAUTHORITY="/home/$SUDO_USER/.Xauthority"
        fi
    fi

    log "Current xrandr outputs:"
    xrandr --query || warn "xrandr query failed; X11 may not be running yet"

    DSI_OUTPUT="$(xrandr --query 2>/dev/null | awk '/^DSI-[0-9]+ connected/ {print $1; exit}')"

    # Clear any stale mode from a connector that was connected before reboot
    # but is disconnected now. This happens on Pi 5 when switching between the
    # two micro-HDMI ports.
    for output in $(xrandr --query 2>/dev/null | awk '/^HDMI.* disconnected/ {print $1}'); do
        xrandr --output "$output" --off 2>/dev/null || true
    done

    HDMI_OUTPUT="$(xrandr --query 2>/dev/null | awk '/^HDMI.* connected/ {print $1; exit}')"
    if [ -n "$HDMI_OUTPUT" ]; then
        log "Enabling connected HDMI output: $HDMI_OUTPUT"
        if [ -n "$DSI_OUTPUT" ]; then
            log "Keeping DSI primary and placing HDMI to the right: $DSI_OUTPUT -> $HDMI_OUTPUT"
            if xrandr --query | awk -v out="$HDMI_OUTPUT" '
                $1 == out {in_output = 1; next}
                in_output && /^[^[:space:]]/ {in_output = 0}
                in_output && $1 == "1920x1080" {found = 1}
                END {exit found ? 0 : 1}
            '; then
                xrandr \
                    --output "$DSI_OUTPUT" --primary --auto --pos 0x0 \
                    --output "$HDMI_OUTPUT" --mode 1920x1080 --rate 60 --right-of "$DSI_OUTPUT" \
                    || warn "Could not enable $HDMI_OUTPUT at 1080p60 with xrandr"
            else
                xrandr \
                    --output "$DSI_OUTPUT" --primary --auto --pos 0x0 \
                    --output "$HDMI_OUTPUT" --auto --right-of "$DSI_OUTPUT" \
                    || warn "Could not enable $HDMI_OUTPUT with xrandr"
            fi
        else
            xrandr --output "$HDMI_OUTPUT" --auto || warn "Could not enable $HDMI_OUTPUT with xrandr"
        fi
    else
        warn "No connected HDMI output found in xrandr. Connect the monitor and rerun this script, or reboot after applying boot config."
    fi

    TOUCH_ID="$(xinput list --id-only "pointer:Goodix Capacitive TouchScreen" 2>/dev/null | head -1 || true)"
    if [ -n "$TOUCH_ID" ] && [ -n "$DSI_OUTPUT" ]; then
        log "Mapping Goodix touchscreen to DSI output: $DSI_OUTPUT"
        xinput map-to-output "$TOUCH_ID" "$DSI_OUTPUT" || warn "Could not map touchscreen to $DSI_OUTPUT"
    fi
else
    warn "xrandr is not installed; boot config was updated only"
fi

log "Done. Reboot is required if HDMI was previously disabled by boot config."
