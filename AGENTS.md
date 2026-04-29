# Repository Guidelines

TSV6 (Topper Stopper V6) — IoT recycling kiosk for Raspberry Pi 5. Barcode scan → AWS IoT lookup → servo door → NFC/QR display. Python 3.11+, `uv` package manager, systemd services on Pi OS Lite (Bookworm).

## Project Structure & Module Organization

```
src/tsv6/
├── core/                  # Application entry points
│   ├── main.py           # EnhancedVideoPlayer + OptimizedBarcodeScanner (VLC/tkinter)
│   ├── production_main.py # ProductionVideoPlayer — wires all subsystems
│   ├── aws_resilient_manager.py  # AWS IoT Core MQTT (retry, circuit breaker)
│   └── image_manager.py  # PIL image loading for tkinter overlays
├── display/               # PiSignage remote display (Hostinger VPS)
│   ├── pisignage_adapter.py   # REST client to PiSignage server
│   ├── playlist_manager.py    # Seeds playlists + assets with duration/ticker
│   └── pisignage_health.py    # Background connection health monitor
├── hardware/              # Hardware abstraction (all have SIMULATION env var fallbacks)
│   ├── barcode_reader.py, servo_*.py, stservo/  # Scanner + servo door
│   ├── sim7600/           # SIM7600NA-H 4G LTE modem (AT commands)
│   ├── nfc/               # PN532 NFC emulator + reader
│   ├── recycle_sensor.py  # VL53L1X ToF — item deposit verification
│   └── tof_sensor.py      # VL53L0X ToF — bin level measurement
├── config/                # Dataclass configs with env var fallbacks
├── utils/                 # Infrastructure: connectivity_manager, lte_monitor, error_recovery, memory_optimizer, etc.
├── services/              # Overlay UI services (status dots, obstruction handler, provisioning)
├── ota/, monitoring/, provisioning/, ui/
tests/
├── unit/                  # Mocked unit tests
├── integration/           # Component interaction tests
└── conftest.py            # Shared fixtures
```

## Build, Test, and Development Commands

```bash
uv sync                              # Install dependencies
uv sync --extra dev                  # Install dev dependencies (pytest, etc.)
uv run pytest                        # Run all tests
uv run pytest --cov=src/tsv6 --cov-report=term-missing  # Tests with coverage
uv run pytest tests/unit/test_aws_manager.py -v          # Specific test file
uv run pytest -n auto                # Parallel test execution
uv run python -c "import tsv6"       # Fast syntax check (no Pi hardware needed)
python main.py                       # Basic video player
python run_production.py             # Production system with full monitoring
```

## Coding Style & Naming Conventions

- **Package imports:** `from tsv6.<module>.<file> import ...`
- **No linter configured** — match surrounding style when editing. Ruff cache exists but no config in `pyproject.toml`.
- **Config pattern:** Dataclass config classes (e.g., `PiSignageConfig`, `ToFSensorConfig`) with env var fallbacks.
- **Manager pattern:** Each subsystem is a class with `connect()`/`disconnect()` or `start()`/`stop()` lifecycle.
- **Threading:** Background monitors use daemon threads with `threading.Event` for shutdown.
- **Secrets:** All credentials from environment variables, never hardcoded. `.env` is gitignored.

## Testing Guidelines

- **Framework:** pytest with pytest-cov, pytest-mock, pytest-asyncio (see `pyproject.toml [tool.pytest.ini_options]`).
- **Test files:** `test_*.py` in `tests/unit/` and `tests/integration/`.
- **Fixtures:** `conftest.py` provides `mock_hardware`, `mock_aws_iot_client`, `mock_network_interfaces`, `temp_config_dir`.
- **Hardware-dependent code** must use simulation mode (e.g., `TSV6_RECYCLE_SENSOR_SIMULATION=true`) or mocking.
- Run `uv run pytest --cov=src/tsv6 --cov-report=term-missing` for coverage.

## Commit & Pull Request Guidelines

Commits follow conventional format: `type(scope): description (#issue)`

- **Types:** `feat`, `fix`, `style`, `docs`, `chore`, `minor`, `refactor`
- **Scopes:** optional, e.g. `player`, `pisignage`, `sensor`
- **Issue tracking:** issue number appended, e.g. `feat: add playlist_manager media/duration/ticker constants #75`
- **PRs:** link to issue, include descriptive summary of changes.

## Architecture Notes

- **Dual display:** VLC/tkinter (local legacy) and PiSignage REST API (remote, current). Feature flag `PISIGNAGE_ENABLED` in `tsv6.service`.
- **Error recovery:** All components register with `ErrorRecoverySystem` for escalation tracking (soft → intermediate → hard → critical).
- **Connectivity:** WiFi/LTE failover via `ConnectivityManager`. LTE 4-stage recovery: re-register → PDP restart → modem restart → GPIO power cycle.
- **Memory:** Pi 4/5 limited RAM. `MemoryOptimizer` runs in production. Avoid large in-memory caches.
- **AWS certs required** in `assets/certs/` — app won't connect without them.

## Key Environment Variables

| Variable | Purpose |
|----------|---------|
| `PISIGNAGE_SERVER_URL` | PiSignage REST API URL |
| `PISIGNAGE_ENABLED` | Feature flag for remote display |
| `TSV6_CONNECTIVITY_MODE` | `wifi_only`, `lte_only`, `wifi_primary_lte_backup`, `lte_primary_wifi_backup` |
| `TSV6_LTE_ENABLED` | Enable SIM7600 modem |
| `TSV6_RECYCLE_SENSOR_SIMULATION` | Bypass ToF hardware for dev |
| `NFC_SERIAL_PORT` | PN532 serial port |

## Things to Know

- **Pi-only hardware:** Servo, NFC, ToF, LTE, barcode scanner need `SIMULATION=true` or mocking off-Pi.
- **PiSignage server:** Deployed separately (Docker on Hostinger VPS). `pisignage/seed_playlists.py` bootstraps playlists.
- **Security:** `tsv6-pi5-setup.sh` has known critical issues — do not deploy without remediation.
- **Deprecated:** `aws_manager.py` — always use `aws_resilient_manager.py` instead.
