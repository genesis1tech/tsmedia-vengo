#!/bin/bash
# Validate Raspberry Pi boot config before TSV6 starts and restore the last good copy if needed.

set -euo pipefail

TARGET="${TSV6_BOOT_CONFIG_PATH:-/boot/firmware/config.txt}"
if [[ "${TSV6_BOOT_CONFIG_PATH:-}" == "" && ! -e "$TARGET" && -e /boot/config.txt ]]; then
    TARGET="/boot/config.txt"
fi
TARGET_DIR="$(dirname "$TARGET")"
LAST_KNOWN_GOOD="${TSV6_BOOT_CONFIG_LAST_KNOWN_GOOD:-$TARGET_DIR/config.txt.last-known-good}"
LOG_FILE="${TSV6_BOOT_CONFIG_GUARD_LOG:-/var/log/tsv6/boot-config-guard.log}"

log() {
    mkdir -p "$(dirname "$LOG_FILE")"
    echo "$(date -Is) $*" | tee -a "$LOG_FILE"
}

validate_config() {
    local file="$1"
    python3 - "$file" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
required = [
    "dtoverlay=vc4-kms-v3d",
    "dtoverlay=vc4-kms-dsi-waveshare-panel-v2,10_1_inch_a",
    "hdmi_force_hotplug=1",
    "hdmi_group=2",
    "hdmi_mode=82",
    "hdmi_drive=2",
]

if not path.exists():
    raise SystemExit(f"{path} is missing")
data = path.read_bytes()
if not data:
    raise SystemExit(f"{path} is empty")
if b"\x00" in data:
    raise SystemExit(f"{path} contains NUL bytes")
try:
    text = data.decode("ascii")
except UnicodeDecodeError as exc:
    raise SystemExit(f"{path} is not ASCII: {exc}") from exc
missing = [line for line in required if line not in text]
if missing:
    raise SystemExit(f"{path} is missing required lines: {', '.join(missing)}")
PY
}

if validate_config "$TARGET" 2>"${LOG_FILE}.tmp"; then
    log "boot config valid: $TARGET"
    rm -f "${LOG_FILE}.tmp"
    exit 0
fi

failure="$(cat "${LOG_FILE}.tmp" 2>/dev/null || true)"
rm -f "${LOG_FILE}.tmp"
log "boot config invalid: $failure"

if [[ ! -f "$LAST_KNOWN_GOOD" ]]; then
    log "last-known-good config missing: $LAST_KNOWN_GOOD"
    exit 1
fi

validate_config "$LAST_KNOWN_GOOD"
cp "$TARGET" "$TARGET.corrupt.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
cp "$LAST_KNOWN_GOOD" "$TARGET"
sync

validate_config "$TARGET"
log "restored boot config from $LAST_KNOWN_GOOD"
