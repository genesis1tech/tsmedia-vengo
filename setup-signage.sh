#!/usr/bin/env bash
# Install the TSV6 signage player on a fresh Raspberry Pi.
#
# Copies the systemd unit and udev rules into place, enables WiFi, reloads
# udev+systemd, and starts the service. Re-runnable (idempotent).
#
# Usage:  ./setup-signage.sh
# Requires: sudo privileges. Run as the user that will own the service
#           (typically the Pi's login user). The service user is baked into
#           tsv6-signage.service (currently `g1tech`); edit that file if the
#           account is different on this device.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UDEV_DIR="/etc/udev/rules.d"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE="tsv6-signage.service"

log()  { printf '\033[0;32m[setup-signage]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup-signage]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31m[setup-signage]\033[0m %s\n' "$*" >&2; exit 1; }

if [[ $EUID -eq 0 ]]; then
  die "Run as a regular user with sudo, not as root."
fi

# --- 1. Dependencies ---------------------------------------------------------
if ! command -v nmcli >/dev/null; then
  die "nmcli not found — install NetworkManager first (sudo apt install network-manager)."
fi
if ! command -v uv >/dev/null; then
  warn "uv not found on PATH — the service's ExecStart expects ~/.local/bin/uv."
fi

# --- 2. Python deps (adds python-xlib needed for the settings menu) ---------
if [[ -f "$SCRIPT_DIR/pyproject.toml" ]] && command -v uv >/dev/null; then
  log "Syncing Python dependencies with uv…"
  (cd "$SCRIPT_DIR" && uv sync)
fi

# --- 3. udev rules -----------------------------------------------------------
log "Installing udev rules for SIM7600 HAT…"
sudo install -m 0644 "$SCRIPT_DIR/scripts/udev/77-tsv6-sim7600-ignore.rules"   "$UDEV_DIR/"
sudo install -m 0644 "$SCRIPT_DIR/scripts/udev/78-tsv6-sim7600-symlinks.rules" "$UDEV_DIR/"
if [[ -f "$SCRIPT_DIR/scripts/udev/99-tsv6-barcode.rules" ]]; then
  sudo install -m 0644 "$SCRIPT_DIR/scripts/udev/99-tsv6-barcode.rules" "$UDEV_DIR/"
fi
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty

# --- 4. systemd unit ---------------------------------------------------------
log "Installing $SERVICE…"
sudo install -m 0644 "$SCRIPT_DIR/$SERVICE" "$SYSTEMD_DIR/$SERVICE"
sudo systemctl daemon-reload

# --- 5. WiFi radio -----------------------------------------------------------
if command -v rfkill >/dev/null; then
  log "Unblocking WiFi rfkill…"
  sudo rfkill unblock wifi
fi
log "Enabling WiFi radio in NetworkManager…"
sudo nmcli radio wifi on || warn "nmcli radio wifi on failed — may already be on."

# --- 6. Enable and start the service ----------------------------------------
log "Enabling $SERVICE to start at boot…"
sudo systemctl enable "$SERVICE"

log "Starting $SERVICE…"
sudo systemctl restart "$SERVICE"

# --- 7. Summary --------------------------------------------------------------
sleep 2
if systemctl is-active --quiet "$SERVICE"; then
  log "$SERVICE is running."
else
  warn "$SERVICE is not active. Inspect: sudo journalctl -u $SERVICE -n 50"
fi

cat <<EOF

Next steps:
  • Long-press the screen for 5 seconds to open the WiFi settings menu.
  • Logs:            sudo journalctl -u $SERVICE -f
  • Service state:   sudo systemctl status $SERVICE
  • Re-run this script any time to reinstall / update artifacts.
EOF
