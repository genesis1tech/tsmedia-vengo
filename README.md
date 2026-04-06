# TSV6 - Raspberry Pi IoT Video Player

TSV6 is a production-ready Raspberry Pi IoT video player system with barcode scanning, AWS IoT integration, servo control, and comprehensive monitoring. It powers recycling kiosks with real-time product identification and door control.

## Quick Start - Installation

### Prerequisites

- Raspberry Pi 5 (8GB RAM) - Recommended
- Raspberry Pi OS Lite (64-bit) - Bookworm
- Waveshare 7" DSI Display (800x480)
- Internet connection
- SSH access configured

### Installation Steps

```bash
# 1. Clone the repository
git clone https://github.com/genesis1tech/tsrpi5.git
cd tsrpi5

# 2. Install system dependencies and Python packages
./setup-dependencies.sh

# 3. Configure Raspberry Pi hardware (DSI display, GPU memory, etc.)
./setup-pi-config.sh

# 4. Install systemd services and user group memberships
./setup-services.sh

# 5. (Optional) Apply security hardening (UFW, fail2ban, SSH)
./setup-security.sh

# 6. Provision AWS IoT certificates
./aws-iot-cert-provisioner.sh

# 7. Reboot to apply all changes
sudo reboot

# 8. Download videos from S3 (requires AWS CLI credentials)
./download_s3_videos.sh

# 9. Download event images from S3
./download_s3_images.sh
```

After reboot, TSV6 will start automatically. Verify with:

```bash
# Check service status
sudo systemctl status tsv6@$USER

# View logs
journalctl -u tsv6@$USER -f

# Use control script
~/tsv6_control.sh status
```

### Setup Scripts Overview

| Script | Purpose | Required |
|--------|---------|----------|
| `deploy.sh` | **Unified deployment** — runs all scripts below in order | Recommended |
| `fleet-deploy.sh` | Fleet management — deploy/update/monitor multiple devices | Optional |
| `first-boot.sh` | Golden image first-boot provisioning | Optional |
| `setup-dependencies.sh` | System packages, Python deps, UV package manager | Yes |
| `setup-pi-config.sh` | Raspberry Pi config (DSI display, GPU, boot settings) | Yes |
| `setup-services.sh` | Systemd services, user groups, diagnostic scripts | Yes |
| `setup-security.sh` | UFW firewall, fail2ban, SSH hardening | Optional |
| `setup-sim7600.sh` | 4G LTE HAT setup (ModemManager, NetworkManager) | Optional |
| `aws-iot-cert-provisioner.sh` | AWS IoT certificate provisioning | Yes |
| `download_s3_videos.sh` | Download videos from S3 bucket | Yes |
| `download_s3_images.sh` | Download event images from S3 bucket | Yes |

### What Each Script Does

**setup-dependencies.sh**
- Installs X11/Xorg for headless display
- Installs VLC with plugins for video playback
- Installs build tools and Python dependencies
- Installs SDL2, image processing, and GPIO libraries
- Installs UV package manager and syncs Python dependencies

**setup-pi-config.sh**
- Enables I2C, SPI, SSH via raspi-config
- Configures Waveshare 7" DSI display in config.txt
- Sets GPU memory to 256MB (optimized for Pi 5)
- Enables PCIe Gen 3 for faster I/O
- Configures CMA (Contiguous Memory Allocator)
- Sets boot target to multi-user.target (console)

**setup-services.sh**
- Adds user to hardware groups (dialout, input, i2c, spi, video)
- Installs tsv6-xorg@.service (X11 server)
- Installs tsv6@.service (main application)
- Creates runtime directories
- Creates diagnostic scripts (display_diagnostics.sh, tsv6_control.sh, test_servo.sh)

**setup-security.sh** (Optional)
- Configures UFW firewall (SSH, MQTT/8883, HTTPS/443)
- Configures fail2ban for SSH brute force protection
- Hardens SSH (disables password auth, root login)

**download_s3_videos.sh**
- Downloads videos from S3 bucket (s3://usc-upstate-videos/)
- Syncs to local assets/videos directory
- Requires AWS CLI credentials configured (`aws configure`)

**download_s3_images.sh**
- Downloads event images from S3 bucket (s3://topper-stopper-event-images/)
- Syncs to local event_images directory
- Requires AWS CLI credentials configured (`aws configure`)

**setup-sim7600.sh** (Optional - for 4G LTE connectivity)
- Installs ModemManager and NetworkManager packages
- Creates udev rules for SIM7600 USB modem
- Configures NetworkManager connection for Hologram.io
- Sets LTE as primary connection (route metric 100 vs WiFi 600)
- Requires Waveshare SIM7600G-H 4G HAT with active SIM card

## Hardware Requirements

| Component | Specification |
|-----------|---------------|
| Board | Raspberry Pi 5 (8GB RAM) |
| Display | Waveshare 7" DSI LCD (800x480) |
| Servo | Waveshare ST3020 via Bus Servo Adapter (USB Serial) |
| Barcode Scanner | USB HID compatible |
| 4G LTE (Optional) | Waveshare SIM7600G-H 4G HAT with Hologram.io SIM |
| Storage | MicroSD 32GB+ (Class 10 or better) |
| Power | 5V/5A USB-C power supply |

## System Requirements

- **OS**: Raspberry Pi OS Lite (64-bit) - Bookworm or newer
- **Python**: 3.11 or later
- **Internet**: Required for AWS IoT and package installation
- **Power**: Stable 5V supply (recommended 5A for Pi 5)

## Key Features

- Video playback with VLC (800x480 DSI display)
- USB HID barcode scanning with sub-100ms latency
- STServo bus servo control via USB serial
- AWS IoT Core integration with MQTT
- Comprehensive error recovery and monitoring
- OTA updates via AWS IoT Jobs
- Memory optimization for production deployment
- WiFi stability monitoring and recovery
- 4G LTE connectivity with WiFi failover

## 4G LTE Configuration (Optional)

TSV6 supports cellular connectivity via the Waveshare SIM7600G-H 4G LTE HAT with automatic WiFi failover.

### Hardware Setup

1. Attach the SIM7600G-H HAT to the Raspberry Pi GPIO header
2. Insert an active SIM card (Hologram.io recommended)
3. Connect the USB cable from the HAT to the Pi

### Software Setup

```bash
# Run the LTE setup script
sudo ./setup-sim7600.sh

# Verify modem is detected
mmcli -L

# Activate the LTE connection
sudo nmcli connection up hologram-lte

# Verify connectivity
ping -c 3 8.8.8.8
```

### Network Connectivity Configuration

TSV6 supports flexible network configuration via environment variables. Edit these in `/etc/systemd/system/tsv6.service` or your service override file.

#### Quick Reference

| Scenario | TSV6_LTE_ENABLED | TSV6_CONNECTIVITY_MODE |
|----------|------------------|------------------------|
| **WiFi Only** (disable LTE) | `false` | `wifi_only` |
| **LTE Only** (disable WiFi) | `true` | `lte_only` |
| **LTE Primary, WiFi Backup** | `true` | `lte_primary_wifi_backup` |
| **WiFi Primary, LTE Backup** | `true` | `wifi_primary_lte_backup` |

#### To Disable 4G/LTE (WiFi Only)

```ini
Environment="TSV6_LTE_ENABLED=false"
Environment="TSV6_CONNECTIVITY_MODE=wifi_only"
```

#### To Use LTE Only (No WiFi)

```ini
Environment="TSV6_LTE_ENABLED=true"
Environment="TSV6_CONNECTIVITY_MODE=lte_only"
```

#### To Use LTE Primary with WiFi Failover (Recommended)

```ini
Environment="TSV6_LTE_ENABLED=true"
Environment="TSV6_LTE_APN=hologram"
Environment="TSV6_CONNECTIVITY_MODE=lte_primary_wifi_backup"
```

#### All LTE Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TSV6_LTE_ENABLED` | `false` | Enable/disable LTE modem |
| `TSV6_LTE_APN` | `hologram` | Cellular APN (Hologram.io default) |
| `TSV6_LTE_PORT` | auto | Serial port (auto-detect if empty) |
| `TSV6_LTE_BAUD` | `115200` | Baud rate for AT commands |
| `TSV6_LTE_FORCE_LTE` | `true` | Force LTE mode (vs 3G/2G) |
| `TSV6_LTE_ROAMING` | `true` | Enable roaming |
| `TSV6_CONNECTIVITY_MODE` | `lte_primary_wifi_backup` | Connection priority mode |

#### Applying Changes

After editing the service file, reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart tsv6.service
```

### Route Priority

When LTE is configured as primary:
- LTE (wwan0): metric 100 (primary)
- WiFi (wlan0): metric 600 (backup)

Traffic automatically fails over to WiFi if LTE disconnects, and fails back when LTE recovers.

### AWS IoT Reporting

When LTE is the active connection, AWS IoT shadow reports:
- `wifiSSID`: "LTE Hologram"
- `wifiStrength`: Signal quality percentage (e.g., "57%")

### Troubleshooting LTE

```bash
# Check modem status
mmcli -m 0

# Check signal quality
mmcli -m 0 | grep "signal quality"

# View NetworkManager connections
nmcli connection show

# Check routing table
ip route

# Restart LTE connection
sudo nmcli connection down hologram-lte
sudo nmcli connection up hologram-lte
```

## Production Deployment

### Single Device — `deploy.sh`

The unified deployment script runs all setup steps in the correct order with a single command.

```bash
# Full interactive deployment
./deploy.sh

# Fully automated (no prompts, auto-reboots when done)
./deploy.sh --non-interactive

# Include 4G LTE modem setup
./deploy.sh --with-lte

# Skip optional steps
./deploy.sh --skip-security    # Skip firewall/SSH hardening
./deploy.sh --skip-aws         # Skip AWS IoT cert provisioning
./deploy.sh --skip-media       # Skip S3 video/image download

# Preview what would run
./deploy.sh --dry-run
```

The script runs these steps automatically:
1. System dependencies (`setup-dependencies.sh`)
2. Pi hardware configuration (`setup-pi-config.sh`)
3. Systemd services (`setup-services.sh`)
4. Security hardening (`setup-security.sh`) — optional
5. LTE modem setup (`setup-sim7600.sh`) — opt-in with `--with-lte`
6. AWS IoT cert provisioning (`aws-iot-cert-provisioner.sh`)
7. S3 media download (videos + images)

Logs are saved to `logs/deploy-<timestamp>.log`.

### Fleet Deployment — `fleet-deploy.sh`

Deploy or manage multiple Raspberry Pi devices from a workstation over SSH.

**Prerequisites:** SSH key-based auth to all devices (`ssh-copy-id user@device`).

```bash
# Create a devices file (one SSH target per line)
cp devices.txt.example devices.txt
# Edit devices.txt with your device IPs/hostnames

# Deploy update to all devices (git pull + uv sync + restart)
./fleet-deploy.sh devices.txt

# Update code only
./fleet-deploy.sh devices.txt --update

# Check status of all devices
./fleet-deploy.sh devices.txt --status

# View logs from all devices
./fleet-deploy.sh devices.txt --logs 50

# Check version on all devices
./fleet-deploy.sh devices.txt --version

# Reboot all devices
./fleet-deploy.sh devices.txt --reboot

# Run arbitrary command on all devices
./fleet-deploy.sh devices.txt --run "df -h /"

# Control parallelism (default: 10 concurrent)
./fleet-deploy.sh devices.txt --status --parallel 20
```

`devices.txt` format:
```
# One SSH target per line (comments and blank lines ignored)
g1tech@192.168.1.10
g1tech@ts-a1b2c3d4.local
pi@10.0.0.50
```

### Golden Image — `first-boot.sh`

For mass deployment using SD card cloning:

1. Flash Raspberry Pi OS Lite 64-bit to an SD card
2. Clone the repo and run the base deployment (skip device-specific steps):
   ```bash
   git clone https://github.com/genesis1tech/tsrpi5.git
   cd tsrpi5
   ./deploy.sh --skip-aws --skip-media
   ```
3. Image that SD card as the "golden image"
4. Flash the golden image to additional SD cards
5. Each device auto-provisions on first boot via `tsv6-first-boot.service`

The first-boot script automatically:
- Sets a unique hostname based on the device serial (`ts-<serial>`)
- Expands the filesystem
- Syncs Python dependencies
- Provisions AWS IoT certificates
- Downloads media from S3
- Enables systemd services
- Writes a provisioning report to `assets/certs/provisioning-report.json`
- Reboots to apply changes

The service is idempotent — it only runs once (marker file `.first-boot-complete`). Use `--force` to re-run.

## Service Management

```bash
# Start/stop/restart TSV6
sudo systemctl start tsv6@$USER
sudo systemctl stop tsv6@$USER
sudo systemctl restart tsv6@$USER

# View status and logs
sudo systemctl status tsv6@$USER
journalctl -u tsv6@$USER -f

# Or use the control script
~/tsv6_control.sh start
~/tsv6_control.sh stop
~/tsv6_control.sh status
~/tsv6_control.sh logs
```

## Diagnostic Scripts

After running `setup-services.sh`, these scripts are available in your home directory:

| Script | Purpose |
|--------|---------|
| `~/display_diagnostics.sh` | Check display, GPU, X11 status |
| `~/tsv6_control.sh` | Start/stop/restart TSV6, view logs |
| `~/test_servo.sh` | Test STServo USB serial connection |

## Development

For development and testing:

```bash
# Install dependencies (including dev)
uv sync --dev

# Run development version
uv run python main.py

# Run production version
uv run python run_production.py

# Run tests
uv run pytest -v --cov=src/tsv6

# View coverage report
uv run pytest -v --cov=src/tsv6 --cov-report=html
```

## Project Structure

```
tsrpi5/
├── deploy.sh                   # Single-command production deployment
├── fleet-deploy.sh             # Multi-device fleet management
├── first-boot.sh               # Golden image first-boot provisioning
├── devices.txt.example         # Example fleet device list
├── setup-dependencies.sh       # System packages + Python deps
├── setup-pi-config.sh          # Raspberry Pi hardware config
├── setup-services.sh           # Systemd services + user groups
├── setup-security.sh           # Security hardening (optional)
├── setup-sim7600.sh            # 4G LTE modem setup (optional)
├── aws-iot-cert-provisioner.sh # AWS IoT certificates
├── tsv6.service                # Main app systemd template
├── tsv6-first-boot.service     # First-boot provisioning service
├── src/tsv6/                   # Main application code
│   ├── core/                   # Main video player, AWS manager
│   ├── hardware/               # Servo, barcode, display drivers
│   ├── config/                 # Configuration management
│   ├── monitoring/             # Watchdog, health monitoring
│   ├── ota/                    # Over-the-air updates
│   └── utils/                  # Utilities, error recovery
├── assets/                     # Videos, images, certificates
├── tests/                      # Unit and integration tests
└── pyproject.toml              # Python dependencies
```

## Documentation

- **[CLAUDE.md](CLAUDE.md)** - Comprehensive development guide
- **[README.md](README.md)** - This file (installation and quick start)

## Troubleshooting

**Display not working:**
```bash
~/display_diagnostics.sh
# Check if X11 is running, GPU memory, DRM devices
```

**Servo not responding:**
```bash
~/test_servo.sh
# Check USB serial device detection
ls -la /dev/ttyUSB* /dev/ttyACM*
```

**Service not starting:**
```bash
journalctl -u tsv6@$USER -n 50 --no-pager
# Check for errors in service startup
```

**Group membership issues (after setup-services.sh):**
```bash
# Log out and back in, or reboot
groups $USER  # Should show: dialout input i2c spi video
```

## Support & Issues

For issues and feature requests, see [GitHub Issues](https://github.com/genesis1tech/tsrpi5/issues).

## License

Proprietary - Genesis 1 Technologies LLC
