#!/bin/bash
# Pi 5 DSI Display Diagnostic Script
# Run this to gather system state for debugging black screen issues

set -e

echo "=== Pi 5 DSI Display Diagnostics ==="
echo "Date: $(date)"
echo "Hostname: $(hostname)"
echo ""

echo "=== HARDWARE INFO ==="
cat /proc/cpuinfo | grep -E "Hardware|Revision|Serial|Model" || echo "N/A"
echo ""

echo "=== DRM CONNECTOR STATUS ==="
for f in /sys/class/drm/card*-*/status; do
    echo "--- $f"
    cat "$f" 2>/dev/null || echo "N/A"
done
echo ""

echo "=== DRM DEVICES ==="
ls -la /dev/dri/ 2>/dev/null || echo "No DRI devices found"
echo ""

echo "=== FRAMEBUFFER ==="
ls -la /dev/fb* 2>/dev/null || echo "No framebuffer devices"
echo "Framebuffer name:"
cat /sys/class/graphics/fb0/name 2>/dev/null || echo "N/A"
echo ""

echo "=== X11 STATUS ==="
echo "X11 sockets:"
ls -la /tmp/.X11-unix/ 2>/dev/null || echo "No X11 sockets found"
echo ""
echo "X server processes:"
pgrep -a Xorg 2>/dev/null || pgrep -a xinit 2>/dev/null || echo "No X server running"
echo ""

echo "=== KERNEL DSI/BACKLIGHT MESSAGES ==="
dmesg | grep -iE "(dsi|backlight|panel|tc358|rp1|vc4|drm)" | tail -30
echo ""

echo "=== KERNEL ERRORS (display related) ==="
dmesg | grep -iE "(dsi|backlight|panel|drm|vc4).*\b(fail|error|warn)" | tail -20 || echo "No errors found"
echo ""

echo "=== CONFIG.TXT DSI/DISPLAY SETTINGS ==="
grep -E "(dtoverlay|dtparam|dsi|display|gpu_mem|i2c|framebuffer|hdmi|cma)" /boot/firmware/config.txt 2>/dev/null || \
grep -E "(dtoverlay|dtparam|dsi|display|gpu_mem|i2c|framebuffer|hdmi|cma)" /boot/config.txt 2>/dev/null || \
echo "Could not read config.txt"
echo ""

echo "=== CMDLINE.TXT ==="
cat /boot/firmware/cmdline.txt 2>/dev/null || cat /boot/cmdline.txt 2>/dev/null || echo "N/A"
echo ""

echo "=== SERVICE STATUS: tsv6-xorg@ ==="
systemctl status 'tsv6-xorg@*' --no-pager -l 2>/dev/null | head -25 || echo "Service not found"
echo ""

echo "=== SERVICE STATUS: tsv6 ==="
systemctl status tsv6.service --no-pager -l 2>/dev/null | head -25 || echo "Service not found"
echo ""

echo "=== I2C BUSES ==="
i2cdetect -l 2>/dev/null || echo "i2cdetect not available"
echo ""

echo "=== LOADED KERNEL MODULES (display) ==="
lsmod | grep -E "(vc4|drm|dsi|panel|tc358|backlight|i2c)" || echo "No relevant modules"
echo ""

echo "=== GPU INFO ==="
vcgencmd get_mem gpu 2>/dev/null || echo "vcgencmd not available"
vcgencmd measure_temp 2>/dev/null || true
vcgencmd get_throttled 2>/dev/null || true
echo ""

echo "=== DEVICE TREE OVERLAYS ==="
dtoverlay -l 2>/dev/null || echo "dtoverlay command not available"
echo ""

# Only run X11 checks if DISPLAY is set
if [ -n "$DISPLAY" ] || [ -S /tmp/.X11-unix/X0 ]; then
    export DISPLAY=${DISPLAY:-:0}
    echo "=== XRANDR OUTPUT ==="
    xrandr --query 2>/dev/null || echo "xrandr failed (DISPLAY=$DISPLAY)"
    echo ""

    echo "=== XDPYINFO ==="
    xdpyinfo 2>/dev/null | head -25 || echo "xdpyinfo failed"
    echo ""
fi

echo "=== MODETEST CONNECTORS ==="
modetest -c 2>/dev/null | head -40 || echo "modetest not installed (apt install libdrm-tests)"
echo ""

echo "=== RECENT JOURNAL ERRORS ==="
journalctl -b -p err --no-pager 2>/dev/null | tail -20 || echo "journalctl not available"
echo ""

echo "=== END OF DIAGNOSTIC REPORT ==="
echo "Save this output and share for debugging."
