#!/bin/bash
# Enable HDMI output alongside the existing Waveshare DSI display.
#
# Boot config writes are delegated to scripts/install-boot-config.sh. This
# script can still turn on a connected HDMI monitor in the running X11 session.

set -euo pipefail

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }
warn() { echo "[WARNING] $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$EUID" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

log "Installing managed boot config with HDMI enabled"
$SUDO bash "$SCRIPT_DIR/install-boot-config.sh"

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
    if XRANDR_OUTPUT="$(xrandr --query 2>/dev/null)"; then
        printf '%s\n' "$XRANDR_OUTPUT"
    else
        XRANDR_OUTPUT=""
        warn "xrandr query failed; X11 may not be running yet"
    fi

    DSI_OUTPUT="$(printf '%s\n' "$XRANDR_OUTPUT" | awk '/^DSI-[0-9]+ connected/ {print $1; exit}')"

    # Clear any stale mode from a connector that was connected before reboot
    # but is disconnected now. This happens on Pi 5 when switching between the
    # two micro-HDMI ports.
    for output in $(printf '%s\n' "$XRANDR_OUTPUT" | awk '/^HDMI.* disconnected/ {print $1}'); do
        xrandr --output "$output" --off 2>/dev/null || true
    done

    HDMI_OUTPUT="$(printf '%s\n' "$XRANDR_OUTPUT" | awk '/^HDMI.* connected/ {print $1; exit}')"
    if [ -n "$HDMI_OUTPUT" ]; then
        log "Enabling connected HDMI output: $HDMI_OUTPUT"
        if [ -n "$DSI_OUTPUT" ]; then
            log "Keeping DSI primary and placing HDMI to the right: $DSI_OUTPUT -> $HDMI_OUTPUT"
            if printf '%s\n' "$XRANDR_OUTPUT" | awk -v out="$HDMI_OUTPUT" '
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
