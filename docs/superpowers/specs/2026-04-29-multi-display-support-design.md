# Multi-Display Support — Design Spec

**Date:** 2026-04-29
**Branch:** TBD (suggested: `feat/multi-display-support`)
**Status:** Approved, ready for implementation plan

---

## Goal

Enable a single TSV6 codebase + golden image to run on three different display configurations, auto-detected at runtime:

| Profile | Connector | Resolution | Orientation | Player |
|---|---|---|---|---|
| `dsi7` | DSI | 800x480 | Landscape | Legacy VLC/tkinter (`core/main.py`) |
| `dsi10` | DSI | 800x1280 | Portrait | PiSignage native player (`display/tsv6_player/`) |
| `hdmi21` | HDMI | 1080x1920 | Portrait (rotated 90°) | PiSignage native player |

The same flashed SD card image must auto-configure correctly when plugged into any of the three displays, with no per-device configuration.

## Non-Goals

- Hot-swap of displays at runtime (a reboot is acceptable when display changes)
- Touch input for the HDMI configuration (the 21" monitor is presumed non-touch)
- Custom landscape layout in the PiSignage native player (the 7" stays on the legacy player)
- Migration of `dsi7` to the PiSignage native player

---

## Architecture

### New Module: `src/tsv6/config/display_profile.py`

Single source of truth for the active display configuration. Loaded once at app startup, passed to all display-aware subsystems.

```python
@dataclass(frozen=True)
class DisplayProfile:
    name: str                                # "dsi7", "dsi10", "hdmi21"
    connector: str                           # "DSI", "HDMI"
    width: int                               # logical pixel width after rotation
    height: int                              # logical pixel height after rotation
    physical_width: int                      # raw connector width (pre-rotation)
    physical_height: int                     # raw connector height (pre-rotation)
    orientation: str                         # "landscape" | "portrait"
    rotation_deg: int                        # 0 | 90 | 180 | 270 — applied via X11 if needed
    player_type: str                         # "legacy" (VLC/tkinter main.py) | "native" (PiSignage)
    vlc_video_rect: tuple[int, int, int, int]    # x, y, w, h in logical pixels
    main_content_rect: tuple[int, int, int, int]
    ticker_rect: tuple[int, int, int, int]


def detect_display_profile() -> DisplayProfile:
    """
    Auto-detect the active display by scanning /sys/class/drm/.

    Detection rules:
    1. If any /sys/class/drm/card*-DSI-*/status contains "connected":
       Read mode from /sys/class/drm/card*-DSI-*/modes (first line = active mode).
       - 800x480  -> dsi7
       - 800x1280 -> dsi10
       - other    -> dsi10 fallback (closest portrait match)
    2. Else if any /sys/class/drm/card*-HDMI-A-*/status contains "connected":
       -> hdmi21
    3. Else: raise DisplayDetectionError (caller decides fallback).

    Returns:
        DisplayProfile populated from PROFILES registry.
    """
```

### Profile Registry

Three pre-defined profiles in `display_profile.py`:

```python
PROFILES = {
    "dsi7": DisplayProfile(
        name="dsi7",
        connector="DSI",
        width=800, height=480,
        physical_width=800, physical_height=480,
        orientation="landscape",
        rotation_deg=0,
        player_type="legacy",
        vlc_video_rect=(0, 0, 800, 390),       # 81.25% of height (matches main.py)
        main_content_rect=(0, 0, 800, 390),
        ticker_rect=(0, 390, 800, 90),
    ),
    "dsi10": DisplayProfile(
        name="dsi10",
        connector="DSI",
        width=800, height=1280,
        physical_width=800, physical_height=1280,
        orientation="portrait",
        rotation_deg=0,
        player_type="native",
        vlc_video_rect=(0, 0, 800, 420),       # current vlc_zone.py default
        main_content_rect=(0, 0, 800, 1220),   # current renderer.py default
        ticker_rect=(0, 1220, 800, 60),
    ),
    "hdmi21": DisplayProfile(
        name="hdmi21",
        connector="HDMI",
        width=1080, height=1920,                # AFTER 90° rotation
        physical_width=1920, physical_height=1080,  # native HDMI orientation
        orientation="portrait",
        rotation_deg=90,                        # X11 xrandr rotates at startup
        player_type="native",
        vlc_video_rect=(0, 0, 1080, 567),       # scaled proportionally from dsi10 (420/1280 * 1920 ≈ 630, but use 1080 width)
        main_content_rect=(0, 0, 1080, 1830),   # 1830 = 1920 - 90 ticker
        ticker_rect=(0, 1830, 1080, 90),
    ),
}
```

Note: HDMI rotation is applied via `xrandr --output HDMI-1 --rotate left` in the Xorg startup hook.

### Override Mechanism

For testing or fleet edge cases, `TSV6_DISPLAY_PROFILE=<name>` environment variable bypasses auto-detection and forces the named profile. Default behaviour is full auto-detection.

---

## Files to Modify

### Python — Application Layer

| File | Change |
|---|---|
| `src/tsv6/config/display_profile.py` | **NEW** — DisplayProfile dataclass, PROFILES, detect_display_profile() |
| `src/tsv6/config/config.py` | DisplayConfig.SCREEN_WIDTH/SCREEN_HEIGHT become dynamic; populated from active profile via factory |
| `src/tsv6/core/main.py` | Replace "Waveshare 7-inch DSI screen" hardcoded text + zone math; use `profile.vlc_video_rect` etc. (legacy player) |
| `src/tsv6/core/production_main.py` | Inject DisplayProfile into subsystems that need dimensions |
| `src/tsv6/display/tsv6_player/renderer.py` | Remove `_DEFAULT_MAIN_RECT = (0, 0, 800, 1220)`; constructor takes a DisplayProfile |
| `src/tsv6/display/tsv6_player/vlc_zone.py` | Remove hardcoded `rect = (0, 0, 800, 420)`; takes profile.vlc_video_rect |
| `src/tsv6/display/tsv6_player/chromium.py` | Remove `width=800, height=1280` defaults; constructor takes profile |
| `src/tsv6/display/tsv6_player/router_page.html` | Viewport meta becomes a Jinja-style placeholder OR served via the existing RouterServer with profile values injected |
| `src/tsv6/display/tsv6_player/router.py` | (If exists) inject viewport width/height into served HTML |
| `src/tsv6/display/tsv6_player/signage_main.py` | Detect profile at startup; refuse to start if `player_type=="legacy"` (point user to `production_main.py`) |
| `src/tsv6/utils/splash_screen.py` | Default DEFAULT_WIDTH/DEFAULT_HEIGHT come from active profile |
| `src/tsv6/utils/sleep_display.py` | Same — defaults from profile |
| `src/tsv6/utils/display_manager.py` | Constructor defaults from profile |
| `src/tsv6/hardware/display_driver_monitor.py` | `_get_display_mode()` and warning patterns become connector-agnostic — detect connector type and emit appropriate patterns |
| `src/tsv6/services/connection_status_indicator.py` | Dot positioning uses profile.width/height |

### Shell / Systemd

| File | Change |
|---|---|
| `tsv6-xorg@.service` | Replace DSI-specific connector check with "any connected DRM connector"; add ExecStartPost hook for HDMI rotation if profile is hdmi21 |
| `gpu-monitor.sh` | Replace `card1-DSI-1` with `card*-{DSI,HDMI-A}-*` glob; iterate connectors |
| `video-watchdog.sh` | Same — connector-agnostic check |
| `gpu-stability-config.sh` | Detect target display at install time (env var or arg); apply DSI settings for DSI, HDMI settings for HDMI; remove the unconditional `hdmi_ignore_hotplug=1` |
| `setup-pi-config.sh` | Take a `--display=dsi7|dsi10|hdmi21` arg; write the appropriate dtoverlay/hdmi config |
| `pisignage/setup_pisignage_player.sh` | Same — display-aware overlay |
| `tsv6-pi5-setup.sh` | Add `--display=` argument; conditional dtoverlay block |
| `scripts/diagnose-display.sh` | Already mostly connector-agnostic; ensure HDMI paths included in greps |

### NOT Changed

- Touch gesture code (`touch_gesture.py`) — unused on HDMI but harmless
- Goodix-specific touch event paths — only active when DSI panel is detected
- AWS, NFC, servo, sensor, LTE code — unrelated
- Test fixtures — already use mocks

---

## Boot-Time Configuration

`/boot/firmware/config.txt` becomes display-conditional. The setup script writes one of three blocks based on `--display=` argument:

**dsi7 block:**
```
# TSV6 dsi7 (Waveshare 7" DSI landscape)
dtoverlay=vc4-kms-v3d,cma-256
dtoverlay=vc4-kms-dsi-7inch
display_auto_detect=0
hdmi_ignore_hotplug=1
gpu_mem=128
```

**dsi10 block:**
```
# TSV6 dsi10 (Waveshare 10.1" DSI portrait)
dtoverlay=vc4-kms-v3d,cma-256
dtoverlay=vc4-kms-dsi-10-1-inch  # exact overlay name TBD-confirmed at impl time
display_auto_detect=0
hdmi_ignore_hotplug=1
gpu_mem=256
```

**hdmi21 block:**
```
# TSV6 hdmi21 (21" HDMI portrait via 90° rotation)
dtoverlay=vc4-kms-v3d,cma-256
display_auto_detect=1
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=82          # 1920x1080 60Hz
hdmi_pixel_freq_limit=400000000
gpu_mem=256
# Rotation handled at X11 layer (xrandr), not boot config
```

The Python auto-detector reads from `/sys/class/drm/`, which reflects the active dtoverlay's connector — so the boot config and runtime detection are consistent.

---

## Detection Flow Diagram

```
App startup (production_main.py or signage_main.py)
    │
    ▼
config.display_profile.detect_display_profile()
    │
    ├── Check TSV6_DISPLAY_PROFILE env var
    │       └── if set, return PROFILES[env]
    │
    ├── Scan /sys/class/drm/card*-DSI-*/status
    │       │
    │       ├── if "connected", read /sys/class/drm/card*-DSI-*/modes
    │       │       ├── 800x480  → return PROFILES["dsi7"]
    │       │       └── 800x1280 → return PROFILES["dsi10"]
    │       │
    │       └── (else fall through)
    │
    ├── Scan /sys/class/drm/card*-HDMI-A-*/status
    │       └── if "connected" → return PROFILES["hdmi21"]
    │
    └── No connector found → raise DisplayDetectionError
            └── Caller logs and falls back to dsi7 (safest default)
```

---

## Player Selection

```
profile = detect_display_profile()

if profile.player_type == "legacy":
    # 7" landscape — current EnhancedVideoPlayer + OptimizedBarcodeScanner path
    from tsv6.core.production_main import ProductionVideoPlayer
    ProductionVideoPlayer(profile=profile).run()
elif profile.player_type == "native":
    # 10.1" or 21" portrait — PiSignage native player
    from tsv6.display.tsv6_player.signage_main import main as signage_main
    signage_main(profile=profile)
```

A small dispatcher in `run_production.py` reads the profile and chooses the entry point.

---

## Testing Strategy

### Unit Tests (`tests/unit/test_display_profile.py` — NEW)

- `test_detect_dsi7_from_modes_file` — mock `/sys/class/drm/card1-DSI-1/modes` returning `800x480` → expect `dsi7`
- `test_detect_dsi10_from_modes_file` — mock returns `800x1280` → expect `dsi10`
- `test_detect_hdmi21_when_only_hdmi_connected` → expect `hdmi21`
- `test_env_var_override_bypasses_detection` → `TSV6_DISPLAY_PROFILE=hdmi21` returns hdmi21 even with DSI connected
- `test_no_connector_raises` → no connected status → `DisplayDetectionError`
- `test_profile_dimensions_match_orientation` → all profiles have orientation matching width/height ratio

### Integration Tests

- `test_renderer_uses_profile_dimensions` — instantiate TSV6Renderer with each profile, verify rects propagate to chromium and vlc_zone
- `test_legacy_player_uses_profile` — EnhancedVideoPlayer constructed with dsi7 profile uses correct zone heights
- `test_router_html_viewport_matches_profile` — RouterServer serves HTML with correct meta viewport for each profile

### Manual Hardware Tests

- Flash same image to 3 Pis with 3 displays attached → all boot to correct profile without manual intervention
- Verify `journalctl -u tsv6.service | grep "DisplayProfile"` logs the auto-detected profile name
- Verify HDMI 90° rotation works (xrandr applied during Xorg startup)

---

## Migration / Backwards Compatibility

- Existing `dsi7` deployments: `detect_display_profile()` returns `dsi7`, legacy player path is identical to today. **Zero behavioural change.**
- New `dsi10` deployments: PiSignage native player uses 800x1280 (same dimensions as today's hardcoded values). **Zero behavioural change once profile is wired through.**
- New `hdmi21` deployments: new code path, requires HDMI dtoverlay block in config.txt + xrandr rotation.

The default profile when detection fails is `dsi7` (current production reality), so any partial rollout is safe.

---

## Risks and Open Questions

1. **Exact 10.1" overlay name** — Waveshare publishes `vc4-kms-dsi-10-1-inch` for some panels and others use bridge chips (e.g. `tc358762`). To be confirmed against the actual hardware revision during implementation. Setup script will accept either via env var.
2. **HDMI rotation method** — `xrandr --rotate left` is the X11 approach. Alternative is `display_rotate=1` in config.txt (fb-level). xrandr is preferred because it doesn't affect boot console rotation, but if VLC rendering has issues with rotated X server, fallback is fb-level rotation.
3. **DRM connector naming** — Pi 5 vs Pi 4 may differ slightly (`card0` vs `card1`). The detection code uses globs to handle both.
4. **Touch on HDMI** — assumed not present. If a touch HDMI monitor is used later, separate work is needed.
5. **PiSignage server-side layout** — the remote PiSignage server may have layouts hardcoded for 800x1280. Switching to hdmi21 may require server-side layout adjustments. Out of scope for this spec.

---

## Acceptance Criteria

1. `detect_display_profile()` returns the correct profile for each of the three hardware configurations.
2. `production_main.py` and `signage_main.py` both use the detected profile; no `800x480`, `800x1280`, or other hardcoded dimensions remain in display-aware code paths.
3. Setup scripts accept a `--display={dsi7,dsi10,hdmi21}` argument and write the correct boot config block.
4. Shell monitoring scripts (`gpu-monitor.sh`, `video-watchdog.sh`) work for both DSI and HDMI connectors.
5. `tsv6-xorg@.service` starts X11 successfully on all three configurations; for hdmi21 it applies xrandr rotation.
6. Unit tests pass; coverage of `display_profile.py` is ≥90%.
7. Manual hardware test: same SD card image boots correctly on all three displays.
