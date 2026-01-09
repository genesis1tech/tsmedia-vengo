# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TSV6 (Topper Stopper V6) is a production-ready IoT video player system for Raspberry Pi hardware. The system displays videos on a Waveshare 7" DSI display, scans barcodes, transmits data to AWS IoT Core, receives product information, and controls a servo motor to open/close a door. It features comprehensive monitoring, error recovery, OTA updates, and production deployment automation.

**Target Hardware:** Raspberry Pi 4B/5 with Waveshare 7" DSI Display
**Python Version:** 3.11+
**Package Manager:** uv
**Testing:** pytest with coverage

## Common Commands

### Development Setup
```bash
# Install dependencies
uv sync

# Install development dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=src/tsv6 --cov-report=term-missing

# Run specific test file
uv run pytest tests/unit/test_aws_manager.py -v

# Run tests in parallel
uv run pytest -n auto
```

### Running the Application
```bash
# Run standard video player
python main.py

# Run production system with enhanced monitoring
python run_production.py

# Run as service (after setup)
sudo systemctl start tsv6.service
sudo systemctl status tsv6.service
sudo systemctl stop tsv6.service
```

### Setup and Deployment
```bash
# Setup autostart service (adds user to dialout group for servo serial access)
./setup_autostart.sh

# Provision AWS IoT certificates
./aws-iot-cert-provisioner.sh

# Download videos from S3
./download_s3_videos.sh

# GPU stability configuration
./gpu-stability-config.sh

# Install video stability patches
./install-video-stability.sh
```

### Monitoring and Diagnostics
```bash
# Monitor GPU temperature
./gpu-monitor.sh

# Video watchdog
./video-watchdog.sh

# Check system monitoring
ls scripts/system_monitoring/

# View logs
tail -f logs/tsv6.log
```

## Code Architecture

### Directory Structure
```
src/tsv6/
├── core/                      # Core application logic
│   ├── main.py               # Main video player with barcode scanning
│   ├── production_main.py    # Production system with monitoring
│   ├── aws_manager.py        # ⚠️ Deprecated AWS manager (use ResilientAWSManager)
│   ├── aws_resilient_manager.py  # Production-ready AWS manager
│   └── image_manager.py      # Image handling for display
│
├── config/                    # Configuration management
│   ├── config.py             # Base configuration
│   ├── config_display.py     # Display settings
│   └── production_config.py  # Production configuration manager
│
├── hardware/                  # Hardware abstraction layer
│   ├── barcode_reader.py     # Barcode scanner interface
│   ├── servo_controller.py   # Base servo controller (DFRobot HAT)
│   ├── servo_controller_enhanced.py
│   ├── servo_manager.py      # Servo management
│   ├── servo_manager_simple.py
│   ├── stservo/              # STServo bus servo controller
│   │   ├── controller.py     # STServo controller wrapper
│   │   └── vendor/           # Waveshare SCServo SDK
│   ├── sim7600/              # 4G LTE modem controller
│   │   ├── controller.py     # SIM7600NA-H HAT controller
│   │   └── at_commands.py    # AT command library & parser
│   ├── nfc/                  # NFC hardware support
│   │   ├── __init__.py       # NFCEmulator export
│   │   ├── nfc_emulator.py   # NFC tag emulation (PN532)
│   │   └── nfc_reader.py     # NFC card reader (PN532)
│   ├── display_driver_monitor.py
│   └── display_fix.py
│
├── monitoring/                # System monitoring
│   └── watchdog_monitor.py   # Watchdog for system health
│
├── ota/                       # Over-The-Air updates
│   └── ota_manager.py        # OTA update manager
│
├── provisioning/              # Device provisioning
│   └── wifi_provisioner.py   # Captive portal WiFi setup
│
├── services/                  # Background services
│   ├── connection_status_indicator.py  # Status dot overlay
│   ├── obstruction_handler.py          # Obstruction UI handler
│   └── wifi_provisioning_ui.py         # Provisioning guide UI
│
└── utils/                     # Utility modules
    ├── connection_check.py
    ├── connection_tracker.py  # AWS connection uptime tracking
    ├── connectivity_manager.py # WiFi/LTE failover orchestrator
    ├── device_manager.py
    ├── display_manager.py
    ├── enhanced_health_monitor.py
    ├── error_recovery.py     # Comprehensive error recovery system
    ├── filesystem_ops.py
    ├── health_monitor.py
    ├── lte_monitor.py        # 4G LTE connectivity monitor
    ├── memory_optimizer.py   # Memory management (Issue #39)
    ├── network_diagnostics.py
    ├── network_monitor.py    # WiFi connectivity monitoring
    ├── process_manager.py    # Child process management
    ├── qr_generator.py
    ├── sleep_display.py      # Sleep mode display
    ├── splash_screen.py      # Startup splash screen
    ├── systemd_recovery_manager.py
    ├── task_manager.py
    └── startup_sequence.py
```

### Core Components

**1. Main Video Player** (`src/tsv6/core/main.py`)
- VLC-based video player with tkinter GUI
- Optimized barcode scanning with threading
- AWS IoT integration for barcode transmission
- Product image display with QR code for NFC URL
- NFC emulator integration for URL broadcasting
- Servo control for door mechanism

**2. Production System** (`src/tsv6/core/production_main.py`)
- Enhanced monitoring and error recovery
- Network monitoring with staged recovery
- AWS connection resilience with retry logic
- System health monitoring
- Memory optimization for Raspberry Pi
- Watchdog monitoring
- OTA update support

**3. Configuration Management**
- `config.py`: Base configuration with dataclasses
- `production_config.py`: ProductionConfigManager for environment-specific settings
- Environment variables via `.env` files

**4. Error Recovery System** (`src/tsv6/utils/error_recovery.py`)
- Multi-level error handling (soft, intermediate, hard, critical)
- Component health tracking
- Automatic recovery with escalation
- Fallback mode for critical failures

**5. Network Monitoring** (`src/tsv6/utils/network_monitor.py`)
- WiFi connectivity monitoring
- Signal strength tracking
- Automatic recovery with systemd integration
- Staged recovery thresholds

**6. Memory Optimization** (`src/tsv6/utils/memory_optimizer.py`)
- Critical for Raspberry Pi with limited memory (Issue #39)
- Memory pressure monitoring
- Automatic garbage collection
- Resource cleanup handlers

**7. 4G LTE System** (`src/tsv6/hardware/sim7600/`)
- Waveshare SIM7600NA-H 4G HAT controller
- AT command protocol with 100+ commands
- Network registration monitoring (2G/3G/LTE)
- Signal quality tracking (RSSI, dBm)
- PDP context management for data connection
- GPIO power control for hard reset (BCM 6)
- Hologram.io APN support (default)
- RNDIS/NDIS USB network interface

**8. LTE Monitor** (`src/tsv6/utils/lte_monitor.py`)
- Background LTE connectivity monitoring
- **Staged Recovery** (4 levels):
  - Soft (2 failures): Network re-registration
  - Intermediate (4 failures): PDP context restart
  - Hard (6 failures): Full modem restart
  - Critical (10 failures): GPIO power cycle
- ModemManager mode (default) or AT command mode
- Integration with ErrorRecoverySystem

**9. Connectivity Manager** (`src/tsv6/utils/connectivity_manager.py`)
- Master orchestrator for WiFi/LTE failover
- **Connectivity Modes**:
  - `wifi_only`: WiFi only
  - `lte_only`: 4G LTE only
  - `wifi_primary_lte_backup`: WiFi primary with LTE fallback
  - `lte_primary_wifi_backup`: LTE primary with WiFi fallback (default)
- Automatic failover with configurable timeout (60s)
- Failback stability check (30s)
- Power saving: auto-disable backup when primary active
- LTE startup wait (90s) with splash screen

**10. Connection Tracker** (`src/tsv6/utils/connection_tracker.py`)
- AWS IoT connection uptime/downtime tracking
- 24-hour rolling window uptime calculation
- **Deadline Monitor**: 30-minute forced reboot if disconnected
- Reconnection attempt counting
- Thread-safe state management

**11. WiFi Provisioner** (`src/tsv6/provisioning/wifi_provisioner.py`)
- Captive portal for first-boot WiFi setup
- Flask web server with credential form
- Hostapd + dnsmasq hotspot configuration
- SSID: `TS_<device-id>`, Password: "recycleit"
- 10-minute configuration timeout

**12. Service Modules** (`src/tsv6/services/`)
- `connection_status_indicator.py`: Colored dot overlay (green=LTE, blue=WiFi, red=none)
- `obstruction_handler.py`: Fullscreen UI for door obstructions
- `wifi_provisioning_ui.py`: QR code provisioning guide

### Key Design Patterns

**1. Production-Ready Architecture**
- Comprehensive error handling at all levels
- Monitoring and alerting for all components
- Graceful degradation and fallback modes
- Automatic recovery with escalation

**2. Threading and Async**
- Optimized barcode scanning with threading
- Queue-based barcode processing
- Concurrent AWS IoT publishing
- Non-blocking UI updates

**3. Dependency Injection**
- Components accept dependencies (e.g., `aws_manager` parameter)
- Testable through mocking
- Flexible configuration

**4. Manager Pattern**
- `AWSManager` / `ResilientAWSManager`
- `OTAManager`
- `MemoryOptimizer`
- Centralized lifecycle management

## Important Development Notes

### ⚠️ Security Issues in Setup Scripts
The `tsv6-pi-setup.sh` script has **35 identified issues** including **6 CRITICAL security vulnerabilities**:
- Hardcoded credentials (Line 46-47)
- SSH keys without passphrases (Line 332, 344)
- SSH key permission issues (Line 614)
- Undefined systemd variables (Line 758-764)
- Unverified downloads (Line 494)

**See documentation files for full details:**
- `README_REVIEW.md` - Navigation guide
- `SECURITY_AND_RELIABILITY_REVIEW.md` - Detailed security analysis
- `REMEDIATION_ACTION_PLAN.md` - Implementation guide
- `QUICK_REFERENCE.md` - Quick fixes
- `ISSUES_DATABASE.md` - Complete issue reference
- `EXECUTIVE_SUMMARY.md` - Executive overview

**⚠️ DO NOT deploy `tsv6-pi-setup.sh` to production without fixing CRITICAL issues**

### Deprecated Components
- `src/tsv6/core/aws_manager.py` - **DEPRECATED**
  - Use `src/tsv6/core/aws_resilient_manager.py` instead
  - Provides enhanced error handling, exponential backoff, circuit breaker pattern

### Testing Strategy
- **Unit tests:** `tests/unit/` - Test individual components with mocking
- **Integration tests:** `tests/integration/` - Test component interactions
- **Fixtures:** `tests/conftest.py` provides comprehensive mocking for hardware, AWS, network

Common test fixtures:
- `mock_hardware` - Mock hardware interfaces
- `mock_aws_iot_client` - Mock AWS IoT client
- `mock_network_interfaces` - Mock network interfaces
- `mock_servo_controller` - Mock servo hardware
- `mock_display` - Mock pygame display
- `sample_barcode_data` - Sample barcode test data

### Environment-Specific Configuration
- Development: Uses default configuration in `config.py`
- Production: Uses `ProductionConfigManager` from `production_config.py`
- Certificates: Stored in `assets/certs/` directory
- Videos: Downloaded from S3 or stored locally

### Key Dependencies
- **Hardware:** adafruit-blinka, adafruit-circuitpython-pca9685, pyserial, rpi-gpio
- **Media:** python-vlc, pygame, Pillow
- **Cloud:** awscrt, awsiotsdk
- **Display:** Custom DSI display drivers
- **Monitoring:** psutil for system metrics

### Raspberry Pi Specific
- Targets Raspberry Pi OS Lite (64-bit) - Bookworm
- Servo control via USB serial adapter (STServo protocol)
- GPU memory configuration critical for video playback
- DSI display requires specific driver setup
- Memory optimization critical (Pi 4 has limited RAM)

### AWS IoT Integration
- MQTT over TLS with certificate authentication
- Topics follow pattern: `device/{thing_name}/...`
- Shadow updates for device state
- Command/response pattern for barcode scanning
- OTA update support via AWS IoT Jobs

### Critical: NFC Emulator Integration
The `OptimizedBarcodeScanner` class in `main.py` **must** include NFC emulator support:
- Import: `from tsv6.hardware.nfc import NFCEmulator` with `NFC_EMULATOR_AVAILABLE` flag
- Initialize `self.nfc_emulator` in `__init__` with callbacks for tag read and status changes
- Stop NFC emulation when new barcode is scanned (prevents stale broadcasts)
- Include `start_nfc_for_transaction(nfc_url, transaction_id)` method called by `production_main.py`
- Stop NFC emulation in `stop_scanning()` method

**If NFC stops working after code changes**, verify these components exist in `main.py`:
1. NFC import block with try/except
2. `self.nfc_emulator` initialization in `OptimizedBarcodeScanner.__init__`
3. `_on_nfc_tag_read` and `_on_nfc_status_change` callback methods
4. NFC stop call before adding new barcode to queue
5. `start_nfc_for_transaction` method in `EnhancedVideoPlayer` class

### Critical: Product Image Overlay (PhotoImage Fix)
The product image overlay in `main.py` requires proper Tkinter PhotoImage handling to prevent "pyimage doesn't exist" errors:

**In `image_manager.py` (`load_image_for_display` method):**
- Accept `master` parameter: `def load_image_for_display(self, image_path, target_size, maintain_aspect_ratio=True, master=None)`
- Pass master to PhotoImage: `photo = ImageTk.PhotoImage(img, master=master)`
- Keep PIL image reference: `photo._pil_image = img`
- Do NOT use context manager (`with Image.open()`) - it closes the image prematurely

**In `main.py` (`_show_image_overlay` method):**
- Pass `master=self.root` to all `load_image_for_display()` calls
- Keep reference on label: `image_label.image = photo`
- Keep reference on overlay: `self.image_overlay.photo = photo`
- Do NOT call `gc.collect()` in overlay hide methods

**If "pyimage1 doesn't exist" error occurs:**
1. Verify `master=self.root` is passed to `load_image_for_display()`
2. Verify `image_label.image = photo` is set after creating the label
3. Remove any `gc.collect()` calls from `_hide_image_overlay` and `_hide_processing_overlay`
4. Ensure `image_manager.py` stores `photo._pil_image = img`

### Known Issues
- **Issue #39:** Memory pressure on Pi 4 - Addressed via `MemoryOptimizer`
- **Issue #48:** Python dependencies in setup script
- Various security issues in deployment scripts (see security review documents)

### Production Deployment Checklist
- [ ] Fix all CRITICAL security issues in setup scripts
- [ ] Configure AWS IoT certificates
- [ ] Add user to dialout group for servo serial access
- [ ] Configure DSI display drivers
- [ ] Download videos from S3
- [ ] Configure autostart service
- [ ] Setup monitoring and logging
- [ ] Test on target hardware (Pi 4B/5)
- [ ] Verify servo operation
- [ ] Test barcode scanning
- [ ] Validate AWS IoT connectivity
- [ ] Test error recovery mechanisms

### Logging and Debugging
- Production system uses structured logging
- Logs written to `logs/tsv6.log`
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Health metrics published to AWS IoT
- Error recovery system tracks component health

### Service Files
| Service | Purpose |
|---------|---------|
| `tsv6.service` | Main TSV6 application |
| `tsv6-wifi-provisioning.service` | WiFi provisioning UI (runs before main) |
| `tsv6-connection-indicator.service` | Status dot overlay |
| `tsv6-obstruction-handler.service` | Obstruction UI handler |
| `tsv6-xorg@.service` | X11 display server template |
| `video-watchdog.service` | Video playback watchdog |
| `sleep.service` | Sleep mode scheduler |

All managed via systemd.

### Environment Variables
**LTE Configuration** (in `tsv6.service`):
```bash
TSV6_LTE_ENABLED=false           # Enable/disable LTE modem
TSV6_LTE_APN=hologram            # APN (Hologram.io default)
TSV6_CONNECTIVITY_MODE=wifi_only # wifi_only, lte_only, wifi_primary_lte_backup, lte_primary_wifi_backup
TSV6_LTE_PORT=/dev/ttyUSB2       # Serial port for modem
TSV6_LTE_BAUD=115200             # Baud rate
TSV6_LTE_FORCE_LTE=true          # Force LTE-only mode (no 3G fallback)
TSV6_LTE_ROAMING=true            # Enable roaming
TSV6_LTE_POWER_GPIO=6            # GPIO pin for modem power control
TSV6_LTE_SIMULATION=false        # Simulation mode for testing
```

**NFC Configuration**:
```bash
NFC_SERIAL_PORT=/dev/ttyUSB5     # PN532 serial port
NFC_BASE_URL=tsrewards--test.expo.app  # Base URL for NFC tags
```

## Troubleshooting

**Memory Issues:**
- Memory optimizer runs automatically in production mode
- Check memory usage: `scripts/system_monitoring/swap_optimizer.sh`
- Manual trigger: Call `optimize_memory_now()` from memory_optimizer

**Network Issues:**
- Network monitor provides automatic recovery
- Check recovery status via `network_monitor.get_recovery_status()`
- Manual network restart: `systemctl restart networking`

**AWS Connection Issues:**
- Verify certificates in `assets/certs/`
- Check AWS IoT endpoint configuration
- Use `ResilientAWSManager` with retry logic
- Monitor connection status via AWS manager status

**Display Issues:**
- Check DSI display driver installation
- Verify GPU memory split in `/boot/config.txt`
- Run display driver monitor for diagnostics
- See `DISPLAY_CONFIG_CHANGES.md` for known display issues

**Servo Issues:**
- Ensure USB serial adapter is connected: `ls /dev/ttyUSB0`
- Ensure user is in dialout group: `groups | grep dialout`
- Check servo ID and baud rate configuration in environment variables
- Test STServo controller: `python -m tsv6.hardware.stservo.controller`

**NFC Emulator Issues:**
- Check NFC serial adapter: `ls /dev/serial/by-id/` for stable path
- Verify NFC emulator initialized: look for `✓ NFC Emulator initialized` in logs
- Check NFC broadcasting: look for `📡 NFC broadcasting URL:` after door closes
- If `'OptimizedBarcodeScanner' object has no attribute 'nfc_emulator'`: restore NFC init code in main.py
- Test NFC emulator: `python -m tsv6.hardware.nfc.nfc_emulator`

**4G LTE Issues:**
- Check modem detection: `ls /dev/ttyUSB*` (should see ttyUSB0-2)
- Check ModemManager: `mmcli -L` to list modems
- Check network connection: `nmcli connection show hologram-lte`
- View signal strength: `mmcli -m 0 --signal-get`
- Check LTE monitor logs: look for `✓ LTE Monitor started` in logs
- Verify APN settings: `TSV6_LTE_APN=hologram` in service file
- Test modem directly: `python -m tsv6.hardware.sim7600.controller`
- If modem unresponsive: GPIO power cycle via `TSV6_LTE_POWER_GPIO=6`

**Connectivity Manager Issues:**
- Check current mode: look for `Connectivity mode:` in startup logs
- Verify failover: look for `Failing over to` messages
- Check connection status indicator: should show colored dot (green=LTE, blue=WiFi)
- Force WiFi-only: set `TSV6_CONNECTIVITY_MODE=wifi_only` in service file
- Check connection tracker: look for uptime percentage in logs

## Development Tips

1. **Always use the Production system** (`run_production.py`) for production-like testing
2. **Mock hardware** in tests using fixtures from `conftest.py`
3. **Monitor memory** when adding features (critical for Pi 4)
4. **Test error recovery** by deliberately failing components
5. **Use ResilientAWSManager** not the deprecated AWSManager
6. **Follow the error recovery pattern** for all new components
7. **Document any security-related changes** (setup scripts especially)
8. **Test on actual Pi hardware** before deployment
9. **Validate OTA update flow** for any configuration changes
10. **Keep setup scripts synchronized** with Python code changes
