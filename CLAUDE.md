# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> See also `AGENTS.md` for project conventions, commit style, and agent-specific hooks. `README.md` covers installation, deploy scripts, and LTE configuration. This file focuses on architecture that requires reading several files to grasp.

## Project Overview

TSV6 (package version 6.2.3, repo `tsmedia-with-player`) is a Raspberry Pi 5 IoT kiosk that scans barcodes, talks to AWS IoT Core, controls a servo door, verifies item deposit via a ToF sensor, broadcasts NFC, and drives a Waveshare 7" DSI display. The display backend is pluggable (PiSignage REST or in-process native Chromium+VLC). A signage-only mode runs the player without any recycling hardware.

## Common Commands

```bash
uv sync                                                  # install
uv sync --extra dev                                      # + pytest, mock
uv run pytest                                            # all tests
uv run pytest tests/unit/test_aws_manager.py -v          # one file
uv run pytest -n auto                                    # parallel (needs pytest-xdist)
uv run pytest --cov=src/tsv6 --cov-report=term-missing   # coverage

python main.py                                           # dev player
python run_production.py                                 # full prod system
python -m tsv6.display.tsv6_player.signage_main          # signage-only (no recycling hw)
```

Service control on a deployed Pi (note the templated unit — `%i` is the user):

```bash
sudo systemctl start  tsv6@$USER
sudo systemctl status tsv6@$USER
journalctl -u tsv6@$USER -f
~/tsv6_control.sh status      # convenience wrapper installed by setup-services.sh
```

Deploy: `./deploy.sh` orchestrates the modular setup scripts (`setup-dependencies.sh`, `setup-pi-config.sh`, `setup-services.sh`, optional `setup-security.sh`, optional `setup-sim7600.sh`, `aws-iot-cert-provisioner.sh`, S3 media). Use `fleet-deploy.sh devices.txt` for multi-device updates and `first-boot.sh` for golden-image provisioning. Details: README.md.

## Architecture

### Display backend abstraction (`src/tsv6/display/`)

`DisplayController` (Protocol in `display/controller.py`) is the contract every backend implements. Production code depends on the protocol — it never imports a concrete backend. Two implementations:

- **`PiSignageAdapter`** (`pisignage_adapter.py`) — REST client to a remote PiSignage server (Hostinger VPS / `tsmedia.g1tech.cloud`). Switches playlists via PiSignage's HTTP API.
- **`TSV6NativeBackend`** (`tsv6_player/backend.py`) — in-process player. Composes a Socket.IO `PlayerProtocolClient` (talks PiSignage 2.x protocol), `AssetSyncer` (pulls assets to a local cache), `TSV6Renderer` (Chromium kiosk on `router_page.html` + VLC zones), and `JSONLImpressionRecorder` (Vistar-compatible logging). The renderer is imported lazily so Flask isn't pulled in until `connect()`.

Selected at runtime via env vars in the systemd unit:

```
PISIGNAGE_ENABLED=true
PISIGNAGE_BACKEND=native        # or "rest"
PISIGNAGE_SERVER_URL=...
PISIGNAGE_INSTALLATION=...
PISIGNAGE_GROUP=...
```

Both backends accept a `playlist_override` argument on `show_deposit_item`, `show_product_display`, `show_no_match`, `show_no_item_detected`, `show_barcode_not_qr`. Backends that can't honor it must silently ignore it. The legacy fullscreen-VLC display path (referenced in older docs) is gone.

#### State-playlist semantics

Transient state screens (`no_item`, `no_match`, `barcode_not_qr`) are MP4 playlists that **play once and return to idle**. Two consequences enforced in code:

1. `setplaylist` events for these names pushed by the PiSignage server are intentionally ignored on the device (commit `c49ffff`) — they would re-trigger the screen out of context.
2. The PiSignage-managed playlist cache must not be clobbered by local writes (commit `62a659b`) — `playlist_manager.py` writes to a separate location.

If you add a new transient state playlist, mirror those guards.

### V2 cloud flow (barcode → playlist override)

A scan published with `flowVersion=v2` flows through:

```
device  ──MQTT──▶  IoT rule barcodeRepoLookupV2
                      ├─▶ Lambda BarcodeRepoLookupV2  (lambdas/barcode_repo_lookup_v2/)
                      │     ├─ DDB brand_playlists  (per-brand override lookup)
                      │     └─ Lambda UpdatedBarcodeToGoUPCV2  (lambdas/updated_barcode_to_go_upc_v2/)
                      └─▶ Kinesis Firehose tsv6-scans-v2
                            └─▶ S3 (Parquet) ─▶ Glue scans_v2 ─▶ Athena view v_scans_v2
```

The brand-playlist override comes back on the IoT response and is threaded through `show_product_display(..., playlist_override=...)`. V1 IoT rule is guarded against double-firing on V2 scans. Definitions live in `infra/aws/`. Spec: `docs/V2_BRAND_PLAYLISTS.md`.

### Connectivity & resilience

- **`ConnectivityManager`** (`utils/connectivity_manager.py`) — orchestrates WiFi / LTE failover. Modes: `wifi_only`, `lte_only`, `wifi_primary_lte_backup`, `lte_primary_wifi_backup`. Configurable failover (60s) and failback stability (30s).
- **`LTEMonitor`** (`utils/lte_monitor.py`) — staged recovery: soft (re-register) → intermediate (PDP restart) → hard (modem restart) → critical (GPIO power cycle on `TSV6_LTE_POWER_GPIO`).
- **`ConnectionTracker`** (`utils/connection_tracker.py`) — tracks AWS IoT uptime; **forces a reboot after 30 minutes disconnected**.
- **`ErrorRecoverySystem`** (`utils/error_recovery.py`) — soft/intermediate/hard/critical escalation per component; new components should plug into it.
- **`MemoryOptimizer`** (`utils/memory_optimizer.py`) — Pi 4/5 RAM is tight; consider memory impact when adding features.

### Threading model

Barcode scanning, AWS IoT, NFC, ToF polling all run on background threads. UI updates go through tkinter on the main thread. Display backends are called from many threads — `TSV6NativeBackend` guards shared state with `_idle_lock`; new shared state needs the same care.

## Module layout (non-obvious bits)

```
src/tsv6/
├── core/                      # main.py (dev), production_main.py (prod), aws_resilient_manager.py
│                              # aws_manager.py is DEPRECATED — use ResilientAWSManager
├── display/
│   ├── controller.py          # DisplayController Protocol
│   ├── pisignage_adapter.py   # REST backend
│   ├── playlist_manager.py    # local cache (do not clobber PiSignage-pushed cache)
│   ├── pisignage_health.py
│   ├── identity.py            # PlayerIdentity (CPU serial, MACs)
│   └── tsv6_player/           # native backend
│       ├── backend.py         # TSV6NativeBackend
│       ├── protocol.py        # Socket.IO 2.x client
│       ├── sync.py            # AssetSyncer
│       ├── renderer.py        # Chromium + VLC zones
│       ├── router.py + router_page.html  # Flask SSE-driven layout
│       ├── chromium.py
│       ├── vlc_zone.py
│       ├── impressions.py + impression_builder.py  # Vistar-compatible logs
│       ├── touch_gesture.py
│       └── signage_main.py    # signage-only entry point
├── hardware/                  # barcode_reader, servo_*, stservo/, sim7600/, nfc/, recycle_sensor
├── ui/modern_theme.py
├── services/                  # connection_status_indicator, obstruction_handler, wifi_provisioning_ui
├── provisioning/wifi_provisioner.py    # captive-portal first-boot
├── monitoring/, ota/, utils/

pisignage/        # server-side (docker-compose, seed_playlists.py, templates) for self-hosted PiSignage
lambdas/          # AWS Lambda v2 source
infra/aws/        # IoT rules, DDB, Glue, Athena, Firehose definitions
docs/             # specs, plans (V2_BRAND_PLAYLISTS.md, NFC_QR_REPLICATION_GUIDE.md, ...)
```

## systemd units

| Unit | Role |
|---|---|
| `tsv6@.service` | Main app (template — `tsv6@$USER`); runs `run_production.py` |
| `tsv6-signage.service` | Signage-only player; runs `tsv6.display.tsv6_player.signage_main` |
| `tsv6-first-boot.service` | One-shot golden-image provisioning (idempotent via `.first-boot-complete` marker) |
| `tsv6-wifi-provisioning.service` | Captive portal before main starts |
| `tsv6-connection-indicator.service` | Status dot overlay (green=LTE, blue=WiFi, red=none) |
| `tsv6-obstruction-handler.service` | Fullscreen UI for door obstructions |
| `tsv6-xorg@.service` | X11 server template |
| `video-watchdog.service` | Video playback watchdog |
| `sleep.service` | Sleep mode scheduler |

`tsv6@.service` runs two pre-flight scripts before the player: `scripts/switch-network-adapter.sh` and `scripts/wifi-wait.sh`. Env-var configuration (LTE, NFC, ToF sensor, connectivity mode) lives in the unit file — not in `.env` for production. README.md has the full table.

## Critical integration hooks (mistakes that have happened before)

### NFC emulator wiring in `core/main.py`
`OptimizedBarcodeScanner` **must** keep all five NFC pieces or stale broadcasts and `'OptimizedBarcodeScanner' object has no attribute 'nfc_emulator'` errors return:
1. `from tsv6.hardware.nfc import NFCEmulator` with `NFC_EMULATOR_AVAILABLE` flag
2. `self.nfc_emulator` initialized in `__init__` with tag-read and status callbacks
3. `_on_nfc_tag_read` and `_on_nfc_status_change` callbacks
4. NFC stop call before adding new barcode to queue (prevents stale broadcast)
5. `start_nfc_for_transaction(nfc_url, transaction_id)` method on `EnhancedVideoPlayer`, plus stop in `stop_scanning`

### Tk PhotoImage lifetime in product overlay
"`pyimage1 doesn't exist`" comes from premature GC. To prevent it:
- `image_manager.load_image_for_display(..., master=self.root)` — pass `master` and forward it to `ImageTk.PhotoImage(img, master=master)`. Stash `photo._pil_image = img`.
- Don't open the PIL image with a `with` block — it closes the file before Tk reads it.
- In `_show_image_overlay`: keep refs on **both** the label (`image_label.image = photo`) and the overlay (`self.image_overlay.photo = photo`).
- Don't call `gc.collect()` from `_hide_image_overlay` / `_hide_processing_overlay`.

### Recycle ToF sensor
Uses `ExtendedI2C(2)`; requires `dtoverlay=i2c2-pi5` in `/boot/firmware/config.txt`. Monitoring window starts **after** the door fully opens and stops **before** it closes — otherwise door motion triggers false positives. Falls back to accepting items without verification if the sensor import fails. Simulation: `TSV6_RECYCLE_SENSOR_SIMULATION=true`.

## Testing

- pytest + pytest-mock + pytest-cov; `tests/unit/`, `tests/integration/`, `tests/hardware/`.
- Hardware fixtures in `tests/conftest.py`: `mock_hardware`, `mock_aws_iot_client`, `mock_servo_controller`, `mock_display`, `mock_network_interfaces`, `sample_barcode_data`. Never assume real hardware in CI.
- Hardware modules import behind `try/except` with a `*_AVAILABLE` flag so tests can run anywhere.

## Deprecated / gotchas

- **`src/tsv6/core/aws_manager.py`** — deprecated; use `aws_resilient_manager.ResilientAWSManager` (exponential backoff, circuit breaker).
- The legacy monolithic `tsv6-pi5-setup.sh` is superseded by the modular `setup-*.sh` scripts driven by `deploy.sh`. Older review docs (`SECURITY_AND_RELIABILITY_REVIEW.md`, etc.) refer to the previous monolithic script — verify whether issues still apply before citing them.
- `production_main.py` defaults to `router_page.html` (not the legacy `custom_layout`). Chromium user-data dir uses `Path.home()`, not hard-coded `/home/pi`.
