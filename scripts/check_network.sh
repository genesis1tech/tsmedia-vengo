#!/bin/bash
# Network diagnostics script for Raspberry Pi
# Checks WiFi, gateway, DNS, and internet connectivity

echo "=== Network Diagnostics ==="
echo "Date: $(date)"
echo ""

# Check network interfaces
echo "--- Network Interfaces ---"
ip addr show | grep -E "^[0-9]+:|inet " | head -20
echo ""

# Check WiFi details
echo "--- WiFi Status ---"
if command -v iwconfig &> /dev/null; then
    iwconfig wlan0 2>/dev/null | grep -E "ESSID|Signal|Bit Rate"
fi
nmcli device status 2>/dev/null || echo "NetworkManager not available"
echo ""

# Check routing
echo "--- Default Gateway ---"
ip route show default
GATEWAY=$(ip route show default | awk '/default/ {print $3}')
echo ""

# Test gateway connectivity
echo "--- Gateway Ping Test ---"
if [ -n "$GATEWAY" ]; then
    ping -c 2 -W 2 "$GATEWAY" 2>&1 | tail -3
else
    echo "No default gateway found"
fi
echo ""

# Check DNS
echo "--- DNS Configuration ---"
cat /etc/resolv.conf | grep -v "^#"
echo ""

# Test internet connectivity (using HTTP since ICMP may be blocked)
echo "--- Internet Connectivity Test ---"
if curl -s --connect-timeout 5 -o /dev/null -w "%{http_code}" https://www.google.com | grep -q "200"; then
    echo "HTTPS connectivity: OK"
else
    echo "HTTPS connectivity: FAILED"
fi

# Get public IP
echo -n "Public IP: "
curl -s --connect-timeout 5 https://api.ipify.org 2>/dev/null || echo "Unable to determine"
echo ""

# Check for cellular modem
echo ""
echo "--- Cellular Modem ---"
if [ -e /dev/ttyACM0 ] || [ -e /dev/ttyUSB0 ]; then
    echo "Modem device detected:"
    ls -la /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
else
    echo "No cellular modem detected"
fi

echo ""
echo "=== Diagnostics Complete ==="
