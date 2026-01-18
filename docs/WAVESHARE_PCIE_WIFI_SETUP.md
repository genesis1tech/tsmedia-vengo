# Waveshare PCIe to M.2 E-Key HAT+ Setup Guide for Raspberry Pi 5

This guide covers the complete installation and configuration of the Waveshare PCIe to M.2 E-Key HAT+ on a Raspberry Pi 5 with an **EDUP WiFi 6E AX210 NGW** wireless card, including disabling the native WiFi.

> **Primary Reference:** This guide is based on Jeff Geerling's excellent blog post:
> [Exploring WiFi 7 (at 2 Gbps) on a Raspberry Pi 5](https://www.jeffgeerling.com/blog/2025/exploring-wifi-7-2-gbps-on-raspberry-pi-5/)
>
> Jeff achieved **1.4 Gbps with external WiFi antennas** and **nearly 2 Gbps** by enabling PCIe Gen 3 mode on a 6 GHz network.

## Hardware Configuration

| Component | Model |
|-----------|-------|
| Single Board Computer | Raspberry Pi 5 |
| PCIe HAT | Waveshare PCIe to M.2 E-Key HAT+ |
| WiFi Card | **EDUP WiFi 6E AX210 NGW** (Intel AX210 chipset) |
| WiFi Standard | WiFi 6E (802.11ax) - 2.4 GHz, 5 GHz, 6 GHz |

## Table of Contents

1. [Hardware Requirements](#hardware-requirements)
2. [Hardware Assembly](#hardware-assembly)
3. [Verify Hardware Detection](#verify-hardware-detection)
4. [Firmware Installation](#firmware-installation)
5. [Set WiFi Regulatory Country](#set-wifi-regulatory-country)
6. [Connect to WiFi Network](#connect-to-wifi-network)
7. [Disable Native WiFi (Required)](#disable-native-wifi-required)
8. [6 GHz Band Configuration](#6-ghz-band-configuration)
9. [Performance Testing](#performance-testing)
10. [Performance Optimization](#performance-optimization)
11. [Bluetooth Setup](#bluetooth-setup)
12. [Troubleshooting](#troubleshooting)

---

## Hardware Requirements

- Raspberry Pi 5
- Waveshare PCIe to M.2 E-Key HAT+ (non-PoE or PoE+ version)
- **EDUP WiFi 6E AX210 NGW** M.2 wireless card (Intel AX210 chipset)
- External WiFi antennas (2x, included with HAT)
- USB adapter cable (included with HAT, for Bluetooth connectivity)
- Raspberry Pi OS (64-bit Bookworm recommended, latest version)

---

## Hardware Assembly

### Step 1: Insert the WiFi Card

1. Locate the M.2 E-Key slot on the Waveshare HAT
2. Insert your **EDUP WiFi 6E AX210 NGW** card at a 30-degree angle
3. Press down gently and secure with the included screw

### Step 2: Connect Antennas

1. Attach the MHF4/IPEX4 antenna cables to the WiFi card
2. Route the cables to the U.FL connectors on the HAT board
3. Connect the external SMA antennas to the antenna jacks

### Step 3: Mount the HAT

1. Align the HAT with the Raspberry Pi 5's 40-pin GPIO header
2. Connect the PCIe FPC ribbon cable to the Pi 5's PCIe connector
3. Secure the HAT using the provided standoffs and screws

### Step 4: Connect Bluetooth USB Adapter (Optional)

As Jeff Geerling notes: "Since the Bluetooth connection is routed through USB pins on the E-key M.2 connector, you must have a HAT that adapts those pins to a header or port you plug into one of the Pi's USB ports. Otherwise Bluetooth will not work."

1. Connect the included USB adapter cable from the HAT to one of the Pi 5's USB 2.0 ports

---

## Verify Hardware Detection

### Check PCIe Device Detection

Boot up the Pi and use `lspci` to check if your card is identified:

```bash
lspci
```

Expected output should show the Intel AX210 WiFi card alongside the Broadcom PCIe bridges and RP1:

```
0000:00:00.0 PCI bridge: Broadcom Inc. and subsidiaries BCM2712 PCIe Bridge (rev 21)
0000:01:00.0 Network controller: Intel Corporation Wi-Fi 6 AX210/AX211/AX411 160MHz (rev 1a)
0001:00:00.0 PCI bridge: Broadcom Inc. and subsidiaries BCM2712 PCIe Bridge (rev 21)
0001:01:00.0 Ethernet controller: Raspberry Pi Ltd RP1 PCIe 2.0 South Bridge
```

### Check Driver Detection

Use `lspci -vv` to verify the Pi identifies the correct iwlwifi kernel module:

```bash
lspci -vv | grep -A 20 "Network controller"
```

You should see:
```
Kernel driver in use: iwlwifi
Kernel modules: iwlwifi
```

**Note:** If you don't see `iwlwifi`, make sure you're on the latest version of Pi OS:
```bash
sudo apt update && sudo apt upgrade -y
```

---

## Firmware Installation

Even with the driver detected, if you run `ip a` or `nmcli`, you won't find the WiFi adapter listed. The firmware still needs to be installed.

### Check Required Firmware

Confirm the exact firmware the Pi is looking for:

```bash
dmesg | grep iwlwifi
```

You'll likely see output like:
```
[    5.104112] Intel(R) Wireless WiFi driver for Linux
[    5.104193] iwlwifi 0000:01:00.0: enabling device (0000 -> 0002)
[    5.124277] iwlwifi 0000:01:00.0: Detected crf-id 0x400410, cnv-id 0x400410 wfpm id 0x80000000
[    5.124300] iwlwifi 0000:01:00.0: PCI dev 2725/0024, rev=0x420, rfid=0x10d000
[    5.124397] iwlwifi 0000:01:00.0: Direct firmware load for iwlwifi-ty-a0-gf-a0-83.ucode failed with error -2
[    5.124413] iwlwifi 0000:01:00.0: Direct firmware load for iwlwifi-ty-a0-gf-a0-82.ucode failed with error -2
[    5.124631] iwlwifi 0000:01:00.0: no suitable firmware found!
```

### Download Firmware from Linux Firmware Repository

The standard `iwlwifi-firmware` package may be out of sync with what's expected. Download directly from the linux-firmware Git repo.

**For the Intel AX210 chipset (used in the EDUP card):**

```bash
# WiFi firmware for AX210
# NOTE: Check your dmesg logs for the exact firmware filename needed!
cd /lib/firmware
sudo wget -O iwlwifi-ty-a0-gf-a0-83.ucode https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/iwlwifi-ty-a0-gf-a0-83.ucode
sudo wget -O iwlwifi-ty-a0-gf-a0.pnvm https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/iwlwifi-ty-a0-gf-a0.pnvm
```

**Alternative:** If you see different firmware filenames in your `dmesg` output, download those specific files from:
`https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/tree/`

### Reboot and Verify

```bash
sudo reboot
```

After reboot, check that the module loaded correctly:

```bash
dmesg | grep iwlwifi
```

Confirm the device is visible with `nmcli`:

```bash
nmcli
```

You should see:
```
wlan1: unavailable
        "Intel Wi-Fi 6 AX210/AX211/AX411 160MHz"
        wifi (iwlwifi), C8:15:4E:26:D3:BF, sw disabled, hw, mtu 1500
```

---

## Set WiFi Regulatory Country

**Important:** If the interface reports as `unavailable`, you haven't set a regulatory WiFi Country. WiFi radios are disabled until you select a country, because different countries have different frequency use regulations.

```bash
sudo raspi-config
```

Navigate to: **Localisation Options** → **WLAN Country** → Select your country

Or use the Pi settings app in the GUI.

After setting the country, the interface should show as `disconnected` in `nmcli`.

---

## Connect to WiFi Network

### Scan for Networks

```bash
nmcli d wifi list
```

### Connect to a Network

```bash
# Connect to a WiFi network on wlan1, the PCIe card
sudo nmcli d wifi connect "ssid_here" password "password_here" ifname wlan1
```

### Verify Connection

```bash
# Show WiFi information and connection details
nmcli device show wlan1
```

Expected output:
```
GENERAL.DEVICE:                         wlan1
GENERAL.TYPE:                           wifi
GENERAL.HWADDR:                         C8:15:4E:26:D3:BF
GENERAL.MTU:                            1500
GENERAL.STATE:                          100 (connected)
...
```

Check connection details:
```bash
iw dev wlan1 info
```

---

## Disable Native WiFi (Required)

**This is critical!** Jeff Geerling discovered that connecting to a 6 GHz network causes issues with the Broadcom WiFi drivers for the internal WiFi chip:

```
[ 1046.305801] brcmfmac: brcmf_set_channel: set chanspec 0xd022 fail, reason -52
```

### Disable Built-in WiFi

Edit the boot configuration:

```bash
sudo nano /boot/firmware/config.txt
```

Add under the `[all]` section:

```ini
[all]
dtoverlay=disable-wifi
```

**Note:** You can also disable Bluetooth if not using the built-in one:
```ini
dtoverlay=disable-bt
```

### Reboot

```bash
sudo reboot
```

---

## 6 GHz Band Configuration

### The 6 GHz Challenge

If you check the WiFi status, you might see it using a 5 or 2.4 GHz band instead of 6 GHz:

```bash
iw dev wlan1 info
```

Since 6 GHz has the shortest wavelength, it typically has the worst signal of the three bands, so the driver often chooses a lower band automatically.

### Force Band Selection (Limited)

NetworkManager allows forcing a band, but only for 2.4 GHz and 5 GHz:

```bash
# Force 5 GHz bands only
sudo nmcli connection modify YOUR_SSID_HERE wifi.band a

# Force 2.4 GHz bands only
sudo nmcli connection modify YOUR_SSID_HERE wifi.band bg
```

**Unfortunately, 6 GHz (ax/be) is not yet supported:**
```bash
$ sudo nmcli connection modify GE_6G wifi.band ax
Error: failed to modify 802-11-wireless.band: 'ax' not among [a, bg].
```

### Solution: Create a Separate 6 GHz-Only SSID

The best workaround is to configure a **separate 6 GHz-only SSID** on your wireless router or AP.

On enterprise APs like the Netgear WBE710 this is straightforward. Consumer APs may vary.

After creating a 6 GHz-only network and connecting:
```bash
$ iw dev wlan1 info
Interface wlan1
    ifindex 3
    wdev 0x1
    addr c8:15:4e:26:d3:bf
    ssid GE_6G
    type managed
    wiphy 0
    channel 5 (5975 MHz), width: 320 MHz, center1: 6105 MHz
    txpower 22.00 dBm
```

### Optional: Upgrade NetworkManager for 6 GHz Display

The Pi's current NetworkManager (1.42.x) doesn't display 6 GHz information. Support was added in version 1.46.

To upgrade (optional, advanced):

```bash
# Edit apt sources
sudo nano /etc/apt/sources.list

# Add Debian testing repository
deb http://deb.debian.org/debian testing main contrib non-free

# Update and upgrade
sudo apt update
sudo apt remove ppp  # Remove conflicting package
sudo apt upgrade -y
sudo reboot

# Verify version
nmcli -v  # Should show 1.46+
```

After upgrading, you'll see 6 GHz support:
```bash
$ nmcli -f wifi-properties dev show wlan1
...
WIFI-PROPERTIES.2GHZ:                   yes
WIFI-PROPERTIES.5GHZ:                   yes
WIFI-PROPERTIES.6GHZ:                   yes
```

---

## Performance Testing

### Install iperf3

```bash
sudo apt install -y iperf3
```

### Test Methodology

Use another computer on your LAN connected via **wired Ethernet** to the router (wireless connections will taint results).

1. On your main computer (wired): `iperf3 -s`
2. On your Pi: `iperf3 -c [server_ip]`

### Example Results (from Jeff Geerling)

**At PCIe Gen 2 with 6 GHz:**
```
$ iperf3 -c 10.0.2.15
...
[  5]   0.00-10.00  sec   534 MBytes   448 Mbits/sec    0             sender
```

**At PCIe Gen 3 with 6 GHz (nearly 2 Gbps!):**
```
$ iperf3 -c 10.0.2.15
...
[  5]   0.00-10.00  sec  2.13 GBytes  1.83 Gbits/sec    0             sender
[  5]   0.00-10.00  sec  2.13 GBytes  1.83 Gbits/sec                  receiver
```

### Install wavemon for Signal Monitoring

The version of wavemon that ships with Pi OS 12 doesn't support the latest Intel WiFi adapters. Build from source:

```bash
sudo apt remove -y wavemon  # If already installed
sudo apt-get -y install pkg-config libncursesw6 libtinfo6 libncurses-dev libnl-cli-3-dev git
git clone https://github.com/uoaerg/wavemon.git
cd wavemon
./configure && make && sudo make install
```

Run wavemon:
```bash
./wavemon -i wlan1
```

Press **F2** for a signal histogram. Use this to find optimal antenna positioning.

### Reasons for Diminished Performance

- **Distance**: RF follows the inverse square law
- **Router settings**: Narrow channel bandwidth, QAM disabled
- **Interference**: Especially on 2.4 or 5 GHz bands
- **Mixed networks**: Pi may choose a more stable but slower frequency

---

## Performance Optimization

### Enable PCIe Gen 3

The Intel AX210 supports PCIe Gen 2x1 speeds, but enabling Gen 3 on the Pi 5 can still improve overall system performance and reduce latency. The Pi 5 defaults to Gen 2 (5 GT/sec).

```bash
sudo nano /boot/firmware/config.txt
```

Add:
```ini
[all]
dtparam=pciex1
dtparam=pciex1_gen=3
```

**Expected WiFi 6E Performance (AX210 on 6 GHz):**
- Typical: 1.0 - 1.4 Gbps with good signal
- Maximum theoretical: ~2.4 Gbps (160 MHz channel width)

**Note:** Actual speeds depend on your router/AP capabilities, signal strength, and interference.

### Check Current PCIe Speed

```bash
sudo lspci -vv | grep -i "lnksta:"
```

Look for "Speed 8GT/s" (Gen 3) or "Speed 5GT/s" (Gen 2).

---

## Bluetooth Setup

The EDUP AX210 includes Bluetooth 5.2 support. The Bluetooth connection is routed through USB pins on the M.2 connector.

### Download Bluetooth Firmware for AX210

```bash
sudo mkdir -p /lib/firmware/intel
cd /lib/firmware/intel

# Bluetooth firmware for AX210
# Check dmesg | grep -i bluetooth for the exact firmware needed
sudo wget -O ibt-0041-0041.ddc https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/intel/ibt-0041-0041.ddc
sudo wget -O ibt-0041-0041.sfi https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/intel/ibt-0041-0041.sfi
sudo reboot
```

**Note:** The firmware version may vary. Check `dmesg | grep -i bluetooth` or `dmesg | grep -i ibt` after boot to see if a different firmware file is required.

### Verify Bluetooth Detection

```bash
hciconfig -a

# Or use bluetoothctl
bluetoothctl
> power on
> agent on
> scan on
```

---

## Troubleshooting

### WiFi Card Not Detected

1. Check PCIe connection:
   ```bash
   lspci | grep -i network
   ```

2. Verify the ribbon cable is properly seated

3. Enable PCIe probe in EEPROM:
   ```bash
   sudo rpi-eeprom-config --edit
   ```
   Add: `PCIE_PROBE=1`

### Driver Loads but No Interface

1. Check if firmware is missing:
   ```bash
   dmesg | grep iwlwifi
   ```
   Look for "firmware load failed" messages

2. Download the specific firmware mentioned in the error

### Interface Shows "Unavailable"

Set your WiFi regulatory country:
```bash
sudo raspi-config
```
Navigate to Localisation Options → WLAN Country

### Disconnections on 6 GHz

Check for Broadcom driver conflicts:
```bash
dmesg | grep brcmfmac
```

If you see errors like `brcmf_set_channel: set chanspec fail`, disable the built-in WiFi:
```bash
sudo nano /boot/firmware/config.txt
# Add: dtoverlay=disable-wifi
sudo reboot
```

### Poor WiFi Performance

1. Check antenna connections
2. Verify PCIe Gen 3 is enabled
3. Use `wavemon` to check signal strength
4. Create a dedicated 6 GHz-only SSID
5. Test in different locations (6 GHz has shorter range)

### No Bluetooth

1. Ensure USB adapter cable is connected from HAT to Pi
2. Download Bluetooth firmware (see Bluetooth Setup section)
3. Check USB detection:
   ```bash
   lsusb | grep -i bluetooth
   ```

---

## Complete config.txt Example

```ini
[all]
# Disable built-in WiFi (required to avoid conflicts with 6 GHz)
dtoverlay=disable-wifi

# Optional: Disable built-in Bluetooth if using AX210's Bluetooth
dtoverlay=disable-bt

# Enable PCIe external connector
dtparam=pciex1

# Force PCIe Gen 3 speeds (recommended for best performance)
dtparam=pciex1_gen=3
```

---

## Quick Reference Commands

| Task | Command |
|------|---------|
| Check PCIe devices | `lspci` |
| Check driver status | `dmesg \| grep iwlwifi` |
| Check WiFi interface | `nmcli` |
| Scan WiFi networks | `nmcli d wifi list` |
| Connect to WiFi | `sudo nmcli d wifi connect "SSID" password "PASS" ifname wlan1` |
| Show connection details | `nmcli device show wlan1` |
| Check WiFi channel/frequency | `iw dev wlan1 info` |
| Monitor signal strength | `wavemon -i wlan1` |
| Test throughput | `iperf3 -c [server_ip]` |
| Set WiFi country | `sudo raspi-config` |
| Edit boot config | `sudo nano /boot/firmware/config.txt` |
| Reboot | `sudo reboot` |

---

## References

### Primary Source
- **[Exploring WiFi 7 (at 2 Gbps) on a Raspberry Pi 5 - Jeff Geerling](https://www.jeffgeerling.com/blog/2025/exploring-wifi-7-2-gbps-on-raspberry-pi-5/)** - The definitive guide with video walkthrough

### Intel AX210 Resources
- [Raspberry Pi PCIe Database - Intel AX210](https://pipci.jeffgeerling.com/cards_network/intel-ax210-wifi-6e.html)
- [GitHub Issue #120 - Test Intel AX210NGW WiFi 6E](https://github.com/geerlingguy/raspberry-pi-pcie-devices/issues/120)
- [Waveshare Wiki - Wireless-AX210](https://www.waveshare.com/wiki/Wireless-AX210)

### Additional Resources
- [Waveshare Wiki - PCIe to M.2 E-Key HAT+](https://www.waveshare.com/wiki/PCIE_TO_M.2_E_KEY_HAT+)
- [Raspberry Pi PCIe Database - Waveshare E-Key HAT](https://pipci.jeffgeerling.com/hats/waveshare-pcie-m2-e-key.html)
- [GitHub Issue #709 - Waveshare PCIe to M.2 E-Key HAT+](https://github.com/geerlingguy/raspberry-pi-pcie-devices/issues/709)
- [Linux Wireless - iwlwifi](https://wireless.wiki.kernel.org/en/users/drivers/iwlwifi)
- [Linux Firmware Git Repository](https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/)
- [Raspberry Pi Official Documentation](https://www.raspberrypi.com/documentation/computers/configuration.html)
