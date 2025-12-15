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
| `setup-dependencies.sh` | System packages, Python deps, UV package manager | Yes |
| `setup-pi-config.sh` | Raspberry Pi config (DSI display, GPU, boot settings) | Yes |
| `setup-services.sh` | Systemd services, user groups, diagnostic scripts | Yes |
| `setup-security.sh` | UFW firewall, fail2ban, SSH hardening | Optional |
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

## Hardware Requirements

| Component | Specification |
|-----------|---------------|
| Board | Raspberry Pi 5 (8GB RAM) |
| Display | Waveshare 7" DSI LCD (800x480) |
| Servo | Waveshare ST3020 via Bus Servo Adapter (USB Serial) |
| Barcode Scanner | USB HID compatible |
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
├── setup-dependencies.sh    # System packages + Python deps
├── setup-pi-config.sh       # Raspberry Pi hardware config
├── setup-services.sh        # Systemd services + user groups
├── setup-security.sh        # Security hardening (optional)
├── aws-iot-cert-provisioner.sh  # AWS IoT certificates
├── src/tsv6/               # Main application code
│   ├── core/               # Main video player, AWS manager
│   ├── hardware/           # Servo, barcode, display drivers
│   ├── config/             # Configuration management
│   ├── monitoring/         # Watchdog, health monitoring
│   ├── ota/                # Over-the-air updates
│   └── utils/              # Utilities, error recovery
├── assets/                 # Videos, images, certificates
├── tests/                  # Unit and integration tests
└── pyproject.toml          # Python dependencies
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
