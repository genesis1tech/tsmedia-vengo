# PiSignage Playlist Trigger from Barcode Scan — Design

**Date:** 2026-04-24
**Scope:** Pi-side (`tsrpi7/tsrpi5`). Backend lambda changes are flagged but out of scope.
**Feature gate:** `PISIGNAGE_ENABLED=true` (existing). VLC fallback path untouched.

## Goal

Preserve the existing barcode → AWS IoT scan pipeline, and route the AWS response into per-campaign PiSignage playlists hosted on the TS Media server (Hostinger VPS). Each barcode can play a different deposit-stage and product-stage playlist, customizable per campaign / school / ad. A short Pi-side QR overlay renders the per-transaction reward URL on top of the product playlist.

## Non-goals

- Changes to the scanner, AWS publish topic, or shadow update payload.
- Changes to servo control or the recycle-sensor verification sequence.
- Changes to the VLC (`PISIGNAGE_ENABLED=false`) fallback display path.
- NFC tag emulation (removed from this flow per the QR-only decision).
- Backend AWS lambda implementation of the new response fields (flagged as a dependency).

## Architecture

```
[Scanner] → [OptimizedBarcodeScanner] → [AWS shadow update]   (UNCHANGED)
                                              │
                                              ▼
                            AWS IoT (lambda/backend)
                                              │
              {... existing fields ..., depositPlaylist?, productPlaylist?}
                                              ▼
[PiSignage Adapter] ◀── [_on_product_image_display]
       │                          │
       │                          ▼
       │                [_verified_door_sequence] (servo + recycle sensor)  (UNCHANGED)
       │                          │
       │                          ▼
       │                [_handle_recycle_success]
       │                          │
       │                          ├── show_product_display(playlist_override=…)
       │                          ├── QrOverlay.show(qr_url, 8s)            (NEW)
       │                          └── _arm_idle_timer(8s)                    (NEW)
       │                                       │
       ▼                                       ▼
   PiSignage REST API            after 8s → show_idle()
```

Three isolated units:
1. **`PiSignageAdapter` overrides** — accept an optional per-stage playlist name; fall back to defaults when absent or invalid.
2. **`QrOverlay`** (new) — Tkinter always-on-top QR window over the PiSignage Chromium kiosk; auto-hides after N seconds.
3. **Idle timer** in `_handle_recycle_success` — cancellable `threading.Timer` that returns the device to `tsv6_idle_loop` after the product-stage window.

## AWS payload (additive)

Existing topic `{thing_name}/openDoor`:

```json
{
  "thingName": "TS_xxxxxxxx",
  "returnAction": "openDoor",
  "barcode": "076406668106",
  "transactionId": "<uuid>",
  "productName": "...",
  "productImage": "https://.../img.jpg",
  "qrUrl": "https://tsrewards--test.expo.app/r/<token>",
  "depositPlaylist": "pepsi_spring26_deposit",
  "productPlaylist": "pepsi_spring26_reward"
}
```

| Field | Type | Required | Default if absent | Stage |
|---|---|---|---|---|
| `depositPlaylist` | string | no | `tsv6_processing` | While door opens / item is deposited |
| `productPlaylist` | string | no | `tsv6_product_display` | After successful deposit, for 8 s |

`noMatch` topic and the device-generated `no_item_detected` event are unchanged. Their playlists remain fixed (`tsv6_no_match`, `tsv6_no_item_detected`).

## Validation

`PiSignageAdapter._resolve_playlist(override, default)`:

1. `None`, empty, non-string → return `default`. Log INFO.
2. Does not match `[A-Za-z0-9_.\-]{1,64}` → return `default`. Log WARNING `"invalid playlist name %r"`.
3. Otherwise → return `override`.

Runtime failure handling in `switch_playlist`:

- HTTP 404 / "playlist not found" on overridden name → log WARNING, fall back to default for that stage. Do **not** abort the transaction.
- 5xx / timeout → existing retry (`max_retries=3`, exponential backoff). On final failure → log ERROR, fall back to default. Continue the transaction.

Per-scan audit log line:

```
INFO scan_response transaction=<uuid> barcode=<...> deposit_playlist=<resolved> product_playlist=<resolved> deposit_override=<bool> product_override=<bool>
```

## Code changes

### `src/tsv6/display/pisignage_adapter.py`

```python
def show_deposit_item(self, playlist_override: str | None = None) -> bool:
    name = self._resolve_playlist(playlist_override, self._config.deposit_playlist)
    return self.switch_playlist(name)

def show_product_display(self, playlist_override: str | None = None) -> bool:
    name = self._resolve_playlist(playlist_override, self._config.product_playlist)
    return self.switch_playlist(name)

def _resolve_playlist(self, override: str | None, default: str) -> str:
    if not override or not isinstance(override, str):
        return default
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", override):
        log.warning("invalid playlist name %r — falling back to %s", override, default)
        return default
    return override
```

The legacy parameters on `show_product_display` (`product_image_path`, `qr_url`, `nfc_url` — all no-ops today) are removed. The QR rendering is now owned entirely by the new `QrOverlay`, called from `_handle_recycle_success` separately. Callers are updated.

### `src/tsv6/display/controller.py`

`DisplayController` Protocol updated to match the new adapter signatures.

### `src/tsv6/display/tsv6_player/backend.py`

`TSV6NativeBackend` mirrors the new signature for parity. Override is accepted; ignored if the native backend has no playlist concept.

### `src/tsv6/core/production_main.py`

`_on_product_image_display` (around line 1407):

```python
deposit_override = product_data.get("depositPlaylist") if isinstance(product_data, dict) else None
self.display_backend.show_deposit_item(playlist_override=deposit_override)
```

`_handle_recycle_success` (around line 1565):

```python
qr_url = product_data.get("qrUrl", "")
product_override = product_data.get("productPlaylist")
self.display_backend.show_product_display(playlist_override=product_override)
self.qr_overlay.show(qr_url, duration_sec=8.0)
self._arm_idle_timer(8.0)
```

New helpers on `ProductionVideoPlayer`:

```python
def _arm_idle_timer(self, seconds: float) -> None:
    if self._idle_timer and self._idle_timer.is_alive():
        self._idle_timer.cancel()
    self._idle_timer = threading.Timer(seconds, self._return_to_idle)
    self._idle_timer.daemon = True
    self._idle_timer.start()

def _return_to_idle(self) -> None:
    try:
        self.qr_overlay.hide()
        if self.display_backend:
            self.display_backend.show_idle()
    except Exception:
        log.exception("idle return failed")
```

`_on_barcode_scanned` cancels the idle timer and hides the QR overlay if a new scan arrives within the 8 s success window.

NFC code paths in PiSignage mode are removed (`start_nfc_for_transaction` is not called from the PiSignage success branch). VLC path NFC behavior is unchanged.

### `src/tsv6/display/qr_overlay.py` (new)

```python
class QrOverlay:
    """Always-on-top Tk window that renders a QR code over the PiSignage kiosk."""
    def __init__(self, size_px: int = 220, position: str = "bottom-right"): ...
    def show(self, url: str, duration_sec: float) -> None: ...   # idempotent
    def hide(self) -> None: ...                                    # idempotent
    def shutdown(self) -> None: ...
```

Implementation notes:
- Owns a private daemon thread running a small Tk root (the main process has no Tk mainloop in PiSignage mode).
- QR generation reuses `tsv6.utils.qr_generator`.
- Auto-hide via `root.after(int(duration_sec * 1000), hide)`.
- `show("")` (empty URL) → no-op.
- If Tk init fails (no `DISPLAY`) → log WARNING, degrade to no-op so the rest of the flow proceeds.
- Position from env (`TSV6_QR_OVERLAY_POSITION` ∈ `bottom-right|bottom-left|top-right|top-left|center`, default `bottom-right`); size from `TSV6_QR_OVERLAY_SIZE_PX` (default `220`).

### Lifecycle

- `QrOverlay` constructed once in `ProductionVideoPlayer._initialize_pisignage()` after `display_backend` is set.
- `QrOverlay.shutdown()` invoked from `ProductionVideoPlayer.shutdown()`.

## Environment variables (new)

| Variable | Default | Purpose |
|---|---|---|
| `TSV6_QR_OVERLAY_POSITION` | `bottom-right` | One of `bottom-right`, `bottom-left`, `top-right`, `top-left`, `center` |
| `TSV6_QR_OVERLAY_SIZE_PX` | `220` | Side length of the QR window in pixels |
| `TSV6_PRODUCT_PLAYLIST_DURATION_SEC` | `8` | Time the product playlist plays before returning to idle |

All optional. Defaults match the agreed behavior (8 s, bottom-right, 220 px).

## Testing

### Unit tests (extend `tests/unit/`)

- `test_pisignage_adapter.py`
  - `_resolve_playlist`: `None` → default; empty → default; valid → name; names containing `/`, `..`, spaces, 65+ chars → default + WARNING.
  - `show_deposit_item(playlist_override="x")` → `switch_playlist("x")`. `show_deposit_item()` → `switch_playlist("tsv6_processing")`.
  - `show_product_display` same matrix.
  - Mocked `requests.post` returning 4xx for an override → falls back to default + WARNING.

- `test_qr_overlay.py`
  - `show()` then `hide()` → window destroyed, timer cancelled.
  - Two consecutive `show()` calls → only one window, prior timer cancelled.
  - No `DISPLAY` env → `show()` returns cleanly, logs WARNING, no exception.
  - QR generator invoked with the supplied URL.

- `test_production_main_pisignage_flow.py`
  - `_on_product_image_display` with `depositPlaylist` → adapter receives override.
  - `_handle_recycle_success` with `productPlaylist` + `qrUrl` → adapter receives override, QR overlay shown, idle timer armed for 8 s.
  - `_handle_recycle_success` with neither override → defaults used.
  - Idle timer fires → `show_idle()` called and QR overlay hidden.
  - New scan within 8 s window → previous idle timer cancelled, QR overlay hidden, processing playlist shown.

All hardware mocked via existing `conftest.py` fixtures.

### Manual integration test (on the Pi)

1. `PISIGNAGE_ENABLED=true` in `tsv6.service`. Restart the service.
2. Scan a known-good barcode whose AWS reply omits the new fields. Confirm: `tsv6_processing` → servo opens → drop item → `tsv6_product_display` with QR overlay for ~8 s → returns to `tsv6_idle_loop`.
3. Scan a barcode whose AWS reply contains both `depositPlaylist` and `productPlaylist` pointing at real playlists on the TS Media server. Confirm overrides take effect.
4. Scan a barcode whose AWS reply contains a bogus playlist name. Confirm fallback to default + WARNING in `logs/tsv6.log`.
5. Scan a second barcode during the 8 s success window. Confirm immediate cut to processing, QR overlay hidden, idle timer cancelled.
6. Restart with `PISIGNAGE_ENABLED=false`. Confirm the original VLC path still works (regression check).

## Edge cases

- AWS response without `qrUrl` → QR overlay `show("")` → no-op; idle timer still runs.
- `display_backend is None` (PiSignage disabled or fallback to VLC) → call sites already null-check; new code paths only run when it exists.
- `no_item_detected` (recycle sensor timeout) → unchanged path; no override; no QR overlay.
- `noMatch` topic → unchanged path; no override; no QR overlay.

## Backend dependency (out of scope)

The AWS lambda owning the `openDoor` topic must be updated to populate `depositPlaylist` and `productPlaylist` in its response when a per-campaign playlist is configured. Until then, the device transparently falls back to defaults — no Pi-side breakage.

## Rollout

- Additive change; defaults reproduce today's exact behavior.
- New `TSV6_QR_OVERLAY_*` and `TSV6_PRODUCT_PLAYLIST_DURATION_SEC` env vars are optional with sensible defaults.
- Feature is already gated behind `PISIGNAGE_ENABLED=true`.

## Files touched

| File | Change |
|---|---|
| `src/tsv6/display/pisignage_adapter.py` | `show_deposit_item`/`show_product_display` accept `playlist_override`; new `_resolve_playlist` helper |
| `src/tsv6/display/controller.py` | Protocol updated to match |
| `src/tsv6/display/tsv6_player/backend.py` | `TSV6NativeBackend` matches new signatures |
| `src/tsv6/display/qr_overlay.py` | **New** |
| `src/tsv6/core/production_main.py` | Two call sites read overrides; `_arm_idle_timer` / `_return_to_idle` helpers; QR overlay lifecycle; remove NFC trigger from PiSignage success branch |
| `tests/unit/test_pisignage_adapter.py` | Extended |
| `tests/unit/test_qr_overlay.py` | **New** |
| `tests/unit/test_production_main_pisignage_flow.py` | **New** |
| `tsv6.service` | (optional) document new env vars in comments |
