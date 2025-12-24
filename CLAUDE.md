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
│   ├── display_driver_monitor.py
│   └── display_fix.py
│
├── monitoring/                # System monitoring
│   └── watchdog_monitor.py   # Watchdog for system health
│
├── ota/                       # Over-The-Air updates
│   └── ota_manager.py        # OTA update manager
│
└── utils/                     # Utility modules
    ├── connection_check.py
    ├── device_manager.py
    ├── display_manager.py
    ├── enhanced_health_monitor.py
    ├── error_recovery.py     # Comprehensive error recovery system
    ├── filesystem_ops.py
    ├── health_monitor.py
    ├── memory_optimizer.py   # Memory management (Issue #39)
    ├── network_diagnostics.py
    ├── network_monitor.py    # Network connectivity monitoring
    ├── qr_generator.py
    ├── systemd_recovery_manager.py
    ├── task_manager.py
    └── startup_sequence.py
```

### Core Components

**1. Main Video Player** (`src/tsv6/core/main.py`)
- VLC-based video player with tkinter GUI
- Optimized barcode scanning with threading
- AWS IoT integration for barcode transmission
- Product image display
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
- `tsv6.service` - Main application service
- `video-watchdog.service` - Watchdog monitoring service
- Both managed via systemd

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
