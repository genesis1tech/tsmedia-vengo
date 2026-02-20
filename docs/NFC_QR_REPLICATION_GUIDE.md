# NFC + QR Code Replication Guide for Raspberry Pi 4

This document describes the complete NFC tag emulation and QR code display system used in TSV6, and how to replicate it on an older Raspberry Pi 4 device.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Hardware Requirements](#hardware-requirements)
3. [Wiring Diagram](#wiring-diagram)
4. [Software Dependencies](#software-dependencies)
5. [Installation Steps](#installation-steps)
6. [Configuration](#configuration)
7. [How It Works: Complete Transaction Flow](#how-it-works-complete-transaction-flow)
8. [NDEF Protocol Details](#ndef-protocol-details)
9. [Code Reference: Key Files](#code-reference-key-files)
10. [Testing & Debugging](#testing--debugging)
11. [Standalone Usage Examples](#standalone-usage-examples)
12. [Troubleshooting](#troubleshooting)

---

## System Overview

The system has two parallel mechanisms to deliver a rewards URL to the user after a successful recycling transaction:

| Method | How it works | User action |
|--------|-------------|-------------|
| **QR Code** | Displayed on-screen as a 350px image | User scans with phone camera |
| **NFC Tag Emulation** | PN532 module broadcasts NDEF URI tag | User taps phone on NFC reader |

Both encode the **same URL**, which is provided by the AWS Lambda backend and contains transaction tracking parameters.

**URL format:**
```
https://tsrewards--test.expo.app/hook?scanid={transaction_id}&barcode={barcode_value}
```

---

## Hardware Requirements

### NFC Module

| Component | Specification |
|-----------|--------------|
| **NFC Module** | PN532 NFC/RFID module |
| **Interface** | HSU (High Speed UART) mode |
| **USB Adapter** | CH340 USB-to-UART adapter |
| **Baud Rate** | 115200 |
| **Protocol** | NFC Forum Type 4 Tag emulation |

### Supported Phones

- **iPhone**: XR and newer (iOS 13+, background NFC reading)
- **Android**: Most NFC-enabled phones (Android 5.0+)

### What You Need to Buy

1. **PN532 NFC Module** - commonly available on Amazon/AliExpress (~$8-15)
2. **CH340 USB-to-Serial Adapter** - if your PN532 doesn't have USB built in (~$3-5)
3. **Jumper wires** (4 wires: VCC, GND, TX, RX) - if using separate adapter

Many PN532 breakout boards come with a built-in CH340 chip and a micro-USB port, so you just plug it directly into the Pi's USB port.

---

## Wiring Diagram

### Option A: PN532 with Built-in USB (Recommended)

```
PN532 Module (USB)  ──USB Cable──>  Raspberry Pi 4 (any USB port)
```

No additional wiring. Plug and play.

### Option B: PN532 + Separate CH340 Adapter

```
PN532 Module         CH340 USB Adapter      Raspberry Pi 4
────────────────────────────────────────────────────────────
VCC  ──────────────> VCC
GND  ──────────────> GND
TX   ──────────────> RX    (cross-wired)
RX   ──────────────> TX    (cross-wired)
                     USB Port ──────────>   Any USB Port
```

**Important:** Set PN532 DIP switches to HSU mode (High Speed UART):
- Switch 1: OFF
- Switch 2: ON

(Check your specific board's datasheet — some use jumper pads instead of DIP switches.)

---

## Software Dependencies

### System Packages

| Package | Purpose |
|---------|---------|
| `libnfc-bin` | Provides `nfc-emulate-forum-tag4` command |
| `libnfc-dev` | Development headers (needed for libnfc configuration) |
| `libusb-1.0-0` | USB communication for PN532 |

### Python Packages

| Package | Version | Purpose |
|---------|---------|---------|
| `pyserial` | >= 3.5 | Serial communication for PN532 wake-up |
| `qrcode` | >= 8.2 | QR code image generation |
| `Pillow` | >= 10.0 | Image processing (QR code rendering) |

---

## Installation Steps

### 1. Install System Packages

```bash
sudo apt-get update
sudo apt-get install -y libnfc-bin libnfc-dev libusb-1.0-0
```

### 2. Configure libnfc

Create or edit `/etc/nfc/libnfc.conf`:

```bash
sudo mkdir -p /etc/nfc
sudo tee /etc/nfc/libnfc.conf << 'EOF'
# libnfc configuration for PN532 via USB/UART (CH340)
allow_autoscan = true
allow_intrusive_scan = false

device.name = "PN532 via USB"
device.connstring = "pn532_uart:/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0:115200"
EOF
```

> **Note:** The `connstring` path may differ on your Pi. See [Finding Your Serial Port](#finding-your-serial-port) below.

### 3. Install Python Packages

```bash
# If using uv (project default)
uv add pyserial qrcode Pillow

# If using pip
pip install pyserial qrcode[pil] Pillow
```

### 4. Set Up USB Permissions

Add your user to the `dialout` group for serial port access:

```bash
sudo usermod -aG dialout $USER
```

Log out and back in (or reboot) for the group change to take effect.

### 5. Verify Hardware Detection

```bash
# Check if PN532 appears as a USB serial device
ls -la /dev/serial/by-id/

# Should show something like:
# usb-1a86_USB_Serial-if00-port0 -> ../../ttyUSB0

# Test libnfc can see the device
nfc-list
# Should output: NXP / PN532 ... firmware version: x.x
```

### 6. Test NFC Emulation

```bash
# Create a test NDEF file and try emulation
echo -ne '\xD1\x01\x0F\x55\x04example.com/test' > /tmp/test_ndef.bin
nfc-emulate-forum-tag4 /tmp/test_ndef.bin
```

If successful, you'll see the module waiting for a phone tap. Tap your phone and it should open `https://example.com/test`.

---

## Configuration

### Environment Variables

Set these in your systemd service file or `.env`:

```bash
# Serial port for the PN532 NFC module
# Use the stable by-id path to survive reboots
NFC_SERIAL_PORT=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0

# Base URL for NFC/QR rewards links (without https:// prefix)
NFC_BASE_URL=tsrewards--test.expo.app/hook
```

### Default Values (if env vars not set)

| Variable | Default |
|----------|---------|
| `NFC_SERIAL_PORT` | `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` |
| `NFC_BASE_URL` | `tsrewards--test.expo.app/hook` |

### Finding Your Serial Port

```bash
# List all USB serial devices
ls -la /dev/serial/by-id/

# Or find ttyUSB devices
ls /dev/ttyUSB*

# Identify which one is the PN532 (plug/unplug and compare)
dmesg | grep -i ch340
```

The stable `/dev/serial/by-id/` path is preferred because `/dev/ttyUSB*` numbers can change between reboots depending on which USB devices are plugged in.

---

## How It Works: Complete Transaction Flow

### Sequence Diagram

```
User          Barcode Scanner    Pi (App)         AWS IoT/Lambda       PN532 NFC       Phone
 |                  |               |                   |                  |              |
 |  Scan barcode    |               |                   |                  |              |
 |----------------->|               |                   |                  |              |
 |                  | barcode data  |                   |                  |              |
 |                  |-------------->|                   |                  |              |
 |                  |               | Publish shadow    |                  |              |
 |                  |               |  update (MQTT)    |                  |              |
 |                  |               |------------------>|                  |              |
 |                  |               |                   |                  |              |
 |                  |               |   openDoor resp   |                  |              |
 |                  |               |   (with nfcUrl)   |                  |              |
 |                  |               |<------------------|                  |              |
 |                  |               |                   |                  |              |
 |                  |               | Open servo door   |                  |              |
 |                  |               |----> [DOOR OPEN]  |                  |              |
 |  Deposit item    |               |                   |                  |              |
 |----> [ToF sensor detects item]   |                   |                  |              |
 |                  |               | Close servo door  |                  |              |
 |                  |               |----> [DOOR CLOSE] |                  |              |
 |                  |               |                   |                  |              |
 |                  |               | Show product      |                  |              |
 |                  |               |  image + QR code  |                  |              |
 |                  |               |----> [DISPLAY]    |                  |              |
 |                  |               |                   |                  |              |
 |                  |               | Start NFC         |                  |              |
 |                  |               |  emulation        |                  |              |
 |                  |               |----------------->| NDEF broadcast   |              |
 |                  |               |                  |  (Type 4 tag)    |              |
 |                  |               |                   |                  |              |
 |                  |               |                   |                  | Tap phone    |
 |                  |               |                   |                  |<-------------|
 |                  |               |                   |                  | Open URL     |
 |                  |               |                   |                  |------------->|
 |                  |               |                   |                  |              |
 |                  |               | 10s timer expires |                  |              |
 |                  |               | Resume video      |                  |              |
```

### Step-by-Step

**1. Barcode Scan**
- USB barcode scanner reads a product barcode
- Any previous NFC emulation is stopped
- Barcode + UUID transaction ID published to AWS IoT shadow update topic

**2. AWS Lambda Response**
- Lambda looks up the barcode in the product database
- Returns JSON on the `{thing_name}/openDoor` MQTT topic:

```json
{
    "thingName": "device-001",
    "returnAction": "openDoor",
    "productName": "Coca-Cola 500ml",
    "productBrand": "Coca-Cola",
    "barcode": "049000042566",
    "nfcUrl": "https://tsrewards--test.expo.app/hook?scanid=a1b2c3d4&barcode=049000042566",
    "transactionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**3. Door Sequence**
- Display "Please Deposit Your Item" screen
- Open servo door
- ToF sensor monitors for item deposit (3-second window)
- Close servo door

**4. Success Path — Display QR + Start NFC**
- Show product image on the left, QR code on the right
- QR code encodes the `nfcUrl` from the AWS response
- 10-second countdown timer displayed
- "Scan For Rewards" text shown below QR
- NFC emulation starts in a background thread, broadcasting the same URL

**5. User Interaction**
- User scans QR code with phone camera, OR
- User taps NFC-enabled phone on the PN532 module
- Either way, the rewards URL opens in their browser

**6. Cleanup**
- After 10 seconds, product overlay hides
- Video playback resumes
- NFC emulation stops (or stops earlier if phone tapped)

---

## NDEF Protocol Details

The NFC tag emulates an **NFC Forum Type 4 Tag** containing an **NDEF URI record**.

### NDEF URI Record Structure

```
Byte   Value   Description
────   ─────   ───────────
0x00   0xD1    Record header (MB=1, ME=1, CF=0, SR=1, IL=0, TNF=001)
                 MB  = Message Begin
                 ME  = Message End
                 SR  = Short Record (payload length fits in 1 byte)
                 TNF = 0x01 (NFC Forum Well-Known Type)
0x01   0x01    Type length = 1 byte
0x02   LEN     Payload length (URL bytes + 1 for prefix code)
0x03   0x55    Type = 'U' (URI record)
0x04   0x04    URI identifier code: 0x04 = "https://"
0x05+  ...     URL bytes (without "https://" prefix)
```

### URI Identifier Codes

| Code | Prefix |
|------|--------|
| 0x00 | (none) |
| 0x01 | http://www. |
| 0x02 | https://www. |
| 0x03 | http:// |
| 0x04 | https:// |

The system uses **0x04** (`https://`) so the URL stored in the NDEF payload is just `tsrewards--test.expo.app/hook?scanid=...` without the protocol prefix.

### Example NDEF Encoding

For URL: `https://tsrewards--test.expo.app/hook?scanid=abc123&barcode=049000042566`

```python
url_without_protocol = "tsrewards--test.expo.app/hook?scanid=abc123&barcode=049000042566"
payload = bytes([0x04]) + url_without_protocol.encode('utf-8')
ndef = bytes([0xD1, 0x01, len(payload), 0x55]) + payload
```

### How Emulation Works

1. NDEF bytes are written to a temporary `.bin` file
2. PN532 is woken up via serial (16x `0x55` bytes at 115200 baud)
3. `nfc-emulate-forum-tag4` subprocess is launched with the `.bin` file
4. The PN532 module acts as a passive NFC Type 4 tag
5. When a phone taps, it reads the NDEF data and opens the URL
6. The subprocess outputs "Target Released" on stderr when a phone disconnects
7. Temp file is cleaned up

---

## Code Reference: Key Files

### NFC Module

| File | Purpose |
|------|---------|
| `src/tsv6/hardware/nfc/__init__.py` | Exports `NFCEmulator`, `PN532`, `emulate_once` |
| `src/tsv6/hardware/nfc/nfc_emulator.py` | NFC Type 4 tag emulation via `nfc-emulate-forum-tag4` |
| `src/tsv6/hardware/nfc/nfc_reader.py` | PN532 card UID reader (HSU protocol over serial) |

### QR Code

| File | Purpose |
|------|---------|
| `src/tsv6/utils/qr_generator.py` | QR code generation with `qrcode` library |

### Integration Points

| File | Class/Method | What it does |
|------|-------------|-------------|
| `src/tsv6/core/main.py:95` | Import block | Imports `NFCEmulator` with `NFC_EMULATOR_AVAILABLE` flag |
| `src/tsv6/core/main.py:86` | Import block | Imports `generate_qr_code` with `QR_GENERATOR_AVAILABLE` flag |
| `src/tsv6/core/main.py:153-166` | `OptimizedBarcodeScanner.__init__` | Initializes NFC emulator with callbacks |
| `src/tsv6/core/main.py:287-289` | Scanner worker | Stops previous NFC before new barcode queued |
| `src/tsv6/core/main.py:1228-1259` | `EnhancedVideoPlayer.display_product_image` | Extracts `nfcUrl` from AWS response, passes to overlay |
| `src/tsv6/core/main.py:1261-1284` | `EnhancedVideoPlayer.start_nfc_for_transaction` | Starts NFC emulation with the AWS-provided URL |
| `src/tsv6/core/main.py:1698-1737` | `_show_image_overlay` (QR section) | Generates and displays QR code on-screen |
| `src/tsv6/core/production_main.py:1222` | `_verified_door_sequence` | Extracts `nfcUrl` from product data |
| `src/tsv6/core/production_main.py:1310-1339` | `_handle_recycle_success` | Triggers NFC + QR after successful deposit |
| `src/tsv6/core/aws_resilient_manager.py:1125-1141` | `_on_barcode_response_received` | Parses AWS `openDoor` response with `nfcUrl` field |

### AWS MQTT Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `$aws/things/{thing}/shadow/update` | Pi -> AWS | Publish scanned barcode |
| `{thing}/openDoor` | AWS -> Pi | Product data + `nfcUrl` response |
| `device/{thing}/recycle/status` | Pi -> AWS | `recycle_success` or `recycle_unsuccess` result |

---

## Testing & Debugging

### Test NFC Emulation Standalone

```bash
# Run the NFC emulator module directly
cd /path/to/tsrpi5
python -m tsv6.hardware.nfc.nfc_emulator
```

This broadcasts a test URL for 120 seconds. Tap your phone to verify it works.

### Test QR Code Generation

```bash
python -m tsv6.utils.qr_generator
# Creates test_qr.png and transaction_qr.png in current directory
```

### Test NFC Emulation Programmatically

```python
from tsv6.hardware.nfc import NFCEmulator

emulator = NFCEmulator(
    base_url="tsrewards--test.expo.app/hook",
    timeout=30
)

# Callback when phone taps
emulator.on_tag_read = lambda sid: print(f"Phone tapped! scanid={sid}")
emulator.on_status_change = lambda status, sid: print(f"Status: {status}")

# Start broadcasting
emulator.start_emulation(scanid="test-123", barcode="049000042566")

# Or with a full URL
emulator.start_emulation_with_url(
    "https://tsrewards--test.expo.app/hook?scanid=test-123&barcode=049000042566",
    identifier="test-123"
)

# Stop when done
emulator.stop_emulation()
```

### Test QR Code Programmatically

```python
from tsv6.utils.qr_generator import generate_qr_code

url = "https://tsrewards--test.expo.app/hook?scanid=test-123&barcode=049000042566"
qr_image = generate_qr_code(url, size=350)
qr_image.save("test_rewards_qr.png")
```

### Verify NFC Reader (Card Reading)

```bash
python -m tsv6.hardware.nfc.nfc_reader
# Hold an NFC card near the PN532 to read its UID
```

---

## Standalone Usage Examples

### Minimal NFC Emulator (No TSV6 Dependencies)

If you want to run NFC emulation on a bare Pi 4 without the full TSV6 system:

```python
#!/usr/bin/env python3
"""Standalone NFC URL broadcaster for Raspberry Pi 4"""

import subprocess
import tempfile
import os
import time

def build_ndef_uri(url_without_https: str) -> bytes:
    """Build NDEF URI record for https:// URL"""
    payload = bytes([0x04]) + url_without_https.encode('utf-8')
    return bytes([0xD1, 0x01, len(payload), 0x55]) + payload

def wake_pn532(port="/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"):
    """Wake PN532 from sleep (HSU mode)"""
    try:
        import serial
        s = serial.Serial(port, 115200, timeout=0.5)
        s.write(b'\x55' * 16)
        time.sleep(0.1)
        s.close()
    except Exception as e:
        print(f"Wake-up warning: {e}")

def broadcast_url(url: str, timeout: int = 120):
    """Broadcast a URL via NFC Type 4 tag emulation"""
    # Strip https:// for NDEF encoding
    if url.startswith("https://"):
        url_body = url[8:]
    else:
        url_body = url

    ndef_data = build_ndef_uri(url_body)

    # Write NDEF to temp file
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.bin', delete=False) as f:
        f.write(ndef_data)
        ndef_file = f.name

    try:
        wake_pn532()

        print(f"Broadcasting: {url}")
        print("Tap your phone to open the URL...")

        proc = subprocess.Popen(
            ['nfc-emulate-forum-tag4', ndef_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = proc.communicate(timeout=timeout)

        if 'Target Released' in stderr:
            print("Phone tapped! URL delivered.")
            return True
        else:
            print("Timeout - no phone detected.")
            return False

    except subprocess.TimeoutExpired:
        proc.kill()
        print("Timeout expired.")
        return False
    except FileNotFoundError:
        print("ERROR: nfc-emulate-forum-tag4 not found.")
        print("Install: sudo apt-get install libnfc-bin")
        return False
    finally:
        os.unlink(ndef_file)

if __name__ == '__main__':
    url = "https://tsrewards--test.expo.app/hook?scanid=test123&barcode=049000042566"
    broadcast_url(url, timeout=60)
```

### Minimal QR Code Generator (No TSV6 Dependencies)

```python
#!/usr/bin/env python3
"""Standalone QR code generator"""

import qrcode
from PIL import Image

def generate_qr(url: str, size: int = 350, output_path: str = "rewards_qr.png"):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img = img.resize((size, size), Image.LANCZOS)
    img.save(output_path)
    print(f"QR code saved to {output_path}")
    return img

if __name__ == '__main__':
    url = "https://tsrewards--test.expo.app/hook?scanid=test123&barcode=049000042566"
    generate_qr(url)
```

---

## Troubleshooting

### `nfc-emulate-forum-tag4` not found

```bash
sudo apt-get install libnfc-bin
```

### `Permission denied` on `/dev/ttyUSB*`

```bash
sudo usermod -aG dialout $USER
# Then log out and back in
```

### PN532 not detected by `nfc-list`

1. Check USB connection: `lsusb | grep -i ch340`
2. Check serial device exists: `ls /dev/ttyUSB*`
3. Verify DIP switches are set to HSU mode
4. Try a different USB port
5. Check dmesg for errors: `dmesg | tail -20`

### NFC works but phone doesn't open URL

1. Ensure URL starts with `https://` (iPhones require HTTPS)
2. Test with a known-good URL first (e.g., `https://google.com`)
3. On iPhone: Settings > NFC must be enabled, and background tag reading requires iOS 13+
4. On Android: NFC must be enabled in Settings > Connected Devices

### QR code too small to scan

- Increase the `size` parameter in `generate_qr_code()` (default is 350px)
- Ensure sufficient contrast (black on white)
- The `error_correction=ERROR_CORRECT_M` setting tolerates ~15% damage

### Serial port changes between reboots

Use the stable `/dev/serial/by-id/` path instead of `/dev/ttyUSB*`:

```bash
ls -la /dev/serial/by-id/
# Use the full path in NFC_SERIAL_PORT env var
```

### Multiple USB serial devices conflict

If you have both a barcode scanner and PN532 on USB:

```bash
# List all USB serial devices with vendor info
for dev in /dev/serial/by-id/*; do
    echo "$dev -> $(readlink -f $dev)"
done
```

The CH340 adapter typically appears as `usb-1a86_USB_Serial-*`.

### NFC emulation starts but phone doesn't detect

1. Hold phone within 2-3cm of the PN532 antenna
2. The NFC antenna on phones is usually on the upper back
3. Try holding the phone steady for 1-2 seconds
4. Some phone cases block NFC — try removing the case
