#!/bin/bash
# Install the TSV6 managed Raspberry Pi boot config with validation and backups.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="${TSV6_BOOT_CONFIG_TEMPLATE:-$REPO_ROOT/config/boot/config.txt.golden}"
TARGET="${TSV6_BOOT_CONFIG_PATH:-}"
BACKUP_DIR="${TSV6_BOOT_CONFIG_BACKUP_DIR:-/home/${SUDO_USER:-${USER:-g1tech}}/boot-config-backups}"

if [[ -z "$TARGET" ]]; then
    if [[ -d /boot/firmware ]]; then
        TARGET="/boot/firmware/config.txt"
    else
        TARGET="/boot/config.txt"
    fi
fi

TARGET_DIR="$(dirname "$TARGET")"
LAST_KNOWN_GOOD="$TARGET_DIR/config.txt.last-known-good"
STAMP="$(date +%Y%m%d_%H%M%S)"
TEMP_FILE="$(mktemp "$TARGET_DIR/.config.txt.tsv6.XXXXXX")"
trap 'rm -f "$TEMP_FILE"' EXIT

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

echo "Installing managed TSV6 boot config"
echo "  template: $TEMPLATE"
echo "  target:   $TARGET"

validate_config "$TEMPLATE"
mkdir -p "$TARGET_DIR" "$BACKUP_DIR"

if [[ -f "$TARGET" ]]; then
    validate_config "$TARGET" || {
        echo "Existing config failed validation; keeping a failure backup before replacement"
    }
    if [[ ! -f "$BACKUP_DIR/config.txt.$STAMP" ]]; then
        cp "$TARGET" "$BACKUP_DIR/config.txt.$STAMP"
        echo "  backup:   $BACKUP_DIR/config.txt.$STAMP"
    fi
fi

cp "$TEMPLATE" "$TEMP_FILE"
validate_config "$TEMP_FILE"

checksum() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

template_sum="$(checksum "$TEMPLATE")"
temp_sum="$(checksum "$TEMP_FILE")"
if [[ "$template_sum" != "$temp_sum" ]]; then
    echo "Template checksum mismatch after temp copy" >&2
    exit 1
fi

cp "$TEMP_FILE" "$TARGET"
cp "$TEMP_FILE" "$LAST_KNOWN_GOOD"
touch "$TARGET_DIR/.metadata_never_index" 2>/dev/null || true
sync

validate_config "$TARGET"
validate_config "$LAST_KNOWN_GOOD"
echo "Managed boot config installed successfully"
