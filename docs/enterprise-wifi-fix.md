# Enterprise WiFi Fix (CoLab / Institutional Networks)

## Problem

Enterprise WiFi networks in universities, government buildings, and other institutions use 802.11k/v/r roaming protocols that disrupt stationary IoT devices. Additionally, many of these networks **block outbound ICMP** to external hosts (8.8.8.8, 1.1.1.1), which causes the TSV6 network watchdog to think the connection is dead and reboot the device every ~5 minutes.

### Symptoms

- Device reboots every 4-5 minutes
- `journalctl -u tsv6-network-watchdog` shows repeated `Ping failed (X/3)` and `STOPPED feeding watchdog, reboot imminent`
- `/bin/ping -c 2 -W 3 8.8.8.8` returns 100% packet loss even though WiFi is connected
- `/bin/ping -c 2 -W 3 <gateway-ip>` succeeds

### Root Cause

Two issues combine:

1. **Blocked outbound ICMP**: The network watchdog only pinged external DNS servers (8.8.8.8, 1.1.1.1). Enterprise networks block these, so the watchdog incorrectly triggers a reboot.

2. **802.11k/v/r roaming disruption**: Enterprise APs request the Pi to perform off-channel radio measurements (802.11k) and send BSS Transition steering requests (802.11v). This causes brief packet drops on a stationary device that has no need to roam.

## Fix Overview

| Fix | What it does | Scope |
|-----|-------------|-------|
| **Gateway-first ping** | Watchdog pings the default gateway before external targets | Universal (in committed code) |
| **bgscan disable** | Stops wpa_supplicant client-side off-channel scanning | Universal (in committed code) |
| **BSSID pin** | Locks WiFi to a specific AP, ignoring steering requests | Per-device (manual) |
| **autoconnect-retries=0** | Infinite reconnect attempts (default is only 4) | Per-device (manual) |

## Steps to Apply on a New Device

### Prerequisites

- Device is connected to the enterprise WiFi network
- SSH access to the device
- The WiFi hardening install script has already been run (`install-wifi-hardening.sh`)

### Step 1: Update the codebase

```bash
cd ~/tsrpi5
git pull
```

This pulls the updated watchdog script (gateway-first ping) and NM config (bgscan disable).

### Step 2: Reinstall WiFi hardening

```bash
sudo scripts/systemd/wifi-hardening/install-wifi-hardening.sh
```

This installs the updated watchdog script and NM config.

### Step 3: Pin BSSID to the nearest AP

Find the current AP BSSID:

```bash
iw dev wlan0 link | grep "Connected to"
```

Output example:
```
Connected to ce:d6:76:fd:b4:f0 (on wlan0)
```

Pin to that BSSID (replace with your actual BSSID):

```bash
CONNECTION_NAME="CoLab"  # Replace with your WiFi connection name
BSSID="CE:D6:76:FD:B4:F0"  # Replace with your AP BSSID

sudo nmcli connection modify "$CONNECTION_NAME" \
    802-11-wireless.bssid "$BSSID" \
    connection.autoconnect-retries 0
```

### Step 4: Verify settings

```bash
# Check BSSID pin and autoconnect-retries
nmcli -f 802-11-wireless.bssid,connection.autoconnect-retries connection show "$CONNECTION_NAME"
# Expected: bssid = your pinned BSSID, autoconnect-retries = 0 (forever)

# Check roamoff (should be 1)
sudo cat /sys/module/brcmfmac/parameters/roamoff
# Expected: 1

# Check power save (should be off)
iw dev wlan0 get power_save
# Expected: Power save: off

# Test gateway ping
GATEWAY=$(ip route show default dev wlan0 | awk '/default/{print $3}')
ping -c 4 -I wlan0 "$GATEWAY"
# Expected: 0% packet loss
```

### Step 5: Reboot and monitor

```bash
sudo reboot
```

After reboot, verify the watchdog is healthy:

```bash
# Should show "OK: network reachable" and running for >5 minutes
systemctl status tsv6-network-watchdog

# Should show gateway in startup log
journalctl -u tsv6-network-watchdog | head -5
```

## Removing the BSSID Pin

If the device is moved to a different location or the AP changes, remove the pin:

```bash
sudo nmcli connection modify "CoLab" 802-11-wireless.bssid ""
```

## Technical Details

### Enterprise AP capabilities detected (CoLab)

```
RM enabled capabilities: Link Measurement, Neighbor Report, Beacon Passive/Active Measurement,
    Channel Load, Statistics Measurement, FTM Range Report
Extended capabilities: BSS Transition, WNM-Sleep Mode, Operating Mode Notification
Authentication suites: PSK, FT/PSK, SAE, FT/SAE
MFP-capable (802.11w)
```

### Why the gateway ping is safe everywhere

- **Normal networks**: Gateway responds, watchdog passes immediately. External pings are still tried as a fallback.
- **Enterprise networks blocking ICMP**: Gateway still responds (it must - it routes traffic). External ping failure doesn't matter.
- **Actual network failure**: Both gateway and external pings fail, correctly triggering the reboot.

### Why bgscan disable is safe for stationary IoT

wpa_supplicant background scanning goes off-channel to find better APs. A stationary kiosk device doesn't need this - it should stay locked to its AP. Disabling bgscan eliminates unnecessary off-channel time that causes packet drops.
