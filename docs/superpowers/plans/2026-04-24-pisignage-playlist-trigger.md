# PiSignage Playlist Trigger from Barcode Scan — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route AWS `openDoor` responses into per-campaign PiSignage playlists via two new optional payload fields (`depositPlaylist`, `productPlaylist`), render the per-transaction QR code as a Pi-side always-on-top overlay for 8 seconds, then return to idle.

**Architecture:** Purely additive on the Pi side. Scan → AWS shadow publish path is untouched. New `playlist_override` kwarg on `PiSignageAdapter.show_deposit_item` / `show_product_display`. New `QrOverlay` module owns the QR window and its timer. New `_arm_idle_timer` / `_return_to_idle` helpers on `ProductionVideoPlayer` drive the 8-second product-stage window. All existing legacy parameters on `show_product_display` (`product_image_path`, `qr_url`, `nfc_url`) are preserved so `TSV6NativeBackend` keeps working unchanged.

**Tech Stack:** Python 3.11, `requests`, `qrcode`, Tkinter (overlay only), pytest, `unittest.mock`. Existing repo conventions: `uv` for env, dataclasses for config, structured logging.

**Refinement from spec (2026-04-24-pisignage-playlist-trigger-design.md):** the spec described removing legacy parameters on `show_product_display`. Implementation keeps them additive instead — `playlist_override` is a new kwarg and the legacy args remain so the alternate `TSV6NativeBackend` (which renders QR + image inline) does not regress. Behaviour is identical for the REST-adapter path used by `PISIGNAGE_ENABLED=true`.

---

## File structure

**Create**

| File | Responsibility |
|---|---|
| `src/tsv6/display/qr_overlay.py` | `QrOverlay` class. Owns a daemon-thread Tk root, generates a QR PNG from a URL via `tsv6.utils.qr_generator.generate_qr_code`, displays it as an always-on-top borderless `Toplevel`, auto-hides after a duration. Idempotent `show()` / `hide()` / `shutdown()`. |
| `tests/unit/test_qr_overlay.py` | Unit tests for `QrOverlay`. |

**Modify**

| File | What changes |
|---|---|
| `src/tsv6/display/pisignage_adapter.py` | Add `_resolve_playlist`. Add `playlist_override` kwarg to `show_deposit_item` and `show_product_display`. Change `PiSignageConfig.deposit_playlist` default from `"tsv6_deposit_item"` to `"tsv6_processing"`. |
| `src/tsv6/display/controller.py` | `DisplayController` Protocol: add `playlist_override` kwarg to `show_deposit_item` and `show_product_display`. |
| `src/tsv6/display/tsv6_player/backend.py` | `TSV6NativeBackend.show_deposit_item` / `show_product_display`: accept `playlist_override` kwarg (ignored — native backend has no per-call playlist switch concept). |
| `src/tsv6/core/production_main.py` | `_on_product_image_display`: extract `depositPlaylist`, pass as override. `_handle_recycle_success`: extract `productPlaylist` + `qrUrl`, pass override, call `qr_overlay.show`, arm idle timer. New helpers `_arm_idle_timer`, `_return_to_idle`. `_on_barcode_scanned`: cancel idle timer + hide overlay. `__init__`: initialize `self._idle_timer = None`. `_initialize_pisignage`: instantiate `self.qr_overlay`. `shutdown`: call `self.qr_overlay.shutdown()` (only if it exists). |
| `tests/unit/test_pisignage_adapter.py` | Add tests for `_resolve_playlist`, override behaviour, default fallback. |
| `tsv6.service` | Document new env vars in inline comments. |

**Test files added/modified**

- `tests/unit/test_qr_overlay.py` — new
- `tests/unit/test_pisignage_adapter.py` — extended
- `tests/unit/test_production_main_pisignage_flow.py` — new (smaller, focused on the new flow only; the existing `test_native_backend.py` and `test_pisignage_adapter.py` keep their scope)

---

## Pre-flight

- [ ] **Step 0a: Verify clean working tree on master**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && git status`
Expected: `nothing to commit, working tree clean` and `On branch master`. If dirty, stash or commit before starting.

- [ ] **Step 0b: Confirm test runner works**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_pisignage_adapter.py -q`
Expected: all existing tests pass. If any pre-existing failures, note them — don't try to fix here.

---

## Task 1: `_resolve_playlist` helper on PiSignageAdapter

**Files:**
- Modify: `src/tsv6/display/pisignage_adapter.py`
- Test: `tests/unit/test_pisignage_adapter.py`

- [ ] **Step 1.1: Write failing tests**

Append to `tests/unit/test_pisignage_adapter.py` (after the existing `TestPiSignageAdapterConvenienceMethods` class):

```python
class TestPiSignageAdapterResolvePlaylist:
    """Validation/fallback for AWS-supplied playlist override names."""

    def test_none_returns_default(self, adapter):
        assert adapter._resolve_playlist(None, "tsv6_processing") == "tsv6_processing"

    def test_empty_string_returns_default(self, adapter):
        assert adapter._resolve_playlist("", "tsv6_processing") == "tsv6_processing"

    def test_non_string_returns_default(self, adapter):
        assert adapter._resolve_playlist(123, "tsv6_processing") == "tsv6_processing"
        assert adapter._resolve_playlist(["x"], "tsv6_processing") == "tsv6_processing"

    def test_valid_name_returns_override(self, adapter):
        assert adapter._resolve_playlist("pepsi_spring26", "tsv6_default") == "pepsi_spring26"

    def test_name_with_dot_dash_underscore_allowed(self, adapter):
        assert adapter._resolve_playlist("a.b-c_1", "tsv6_default") == "a.b-c_1"

    def test_name_with_slash_falls_back(self, adapter, caplog):
        with caplog.at_level("WARNING"):
            assert adapter._resolve_playlist("../etc/passwd", "tsv6_default") == "tsv6_default"
        assert "invalid playlist name" in caplog.text

    def test_name_with_space_falls_back(self, adapter):
        assert adapter._resolve_playlist("bad name", "tsv6_default") == "tsv6_default"

    def test_name_too_long_falls_back(self, adapter):
        assert adapter._resolve_playlist("x" * 65, "tsv6_default") == "tsv6_default"

    def test_max_length_64_allowed(self, adapter):
        name = "x" * 64
        assert adapter._resolve_playlist(name, "tsv6_default") == name
```

- [ ] **Step 1.2: Run tests to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_pisignage_adapter.py::TestPiSignageAdapterResolvePlaylist -v`
Expected: 9 tests fail with `AttributeError: 'PiSignageAdapter' object has no attribute '_resolve_playlist'`.

- [ ] **Step 1.3: Add `_resolve_playlist` to `PiSignageAdapter`**

In `src/tsv6/display/pisignage_adapter.py`:

Find the import block at the top of the file and add `re` if not already imported:
```python
import re
```

Inside `class PiSignageAdapter:` add a new private method (place it directly above the existing `def show_idle` / convenience-methods section — search for `# ── Convenience` or place after `switch_playlist`):

```python
_VALID_PLAYLIST_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,64}")

def _resolve_playlist(self, override: str | None, default: str) -> str:
    """Validate an AWS-supplied playlist name; fall back to ``default`` if absent or unsafe."""
    if not override or not isinstance(override, str):
        return default
    if not self._VALID_PLAYLIST_NAME.fullmatch(override):
        logger.warning(
            "invalid playlist name %r — falling back to %s", override, default
        )
        return default
    return override
```

- [ ] **Step 1.4: Run tests to verify pass**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_pisignage_adapter.py::TestPiSignageAdapterResolvePlaylist -v`
Expected: all 9 tests pass.

- [ ] **Step 1.5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/display/pisignage_adapter.py tests/unit/test_pisignage_adapter.py
git commit -m "feat(pisignage): add _resolve_playlist with regex validation + fallback"
```

---

## Task 2: `show_deposit_item` override + change deposit default

**Files:**
- Modify: `src/tsv6/display/pisignage_adapter.py`
- Test: `tests/unit/test_pisignage_adapter.py`

- [ ] **Step 2.1: Write failing tests**

Append to `tests/unit/test_pisignage_adapter.py`:

```python
class TestPiSignageAdapterDepositOverride:
    """show_deposit_item respects the optional playlist_override kwarg."""

    @patch("requests.post")
    def test_default_uses_tsv6_processing(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        connected_adapter.show_deposit_item()
        called_url = mock_post.call_args[0][0]
        assert "setplaylist/player123/tsv6_processing" in called_url

    @patch("requests.post")
    def test_override_takes_precedence(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        connected_adapter.show_deposit_item(playlist_override="pepsi_spring26_deposit")
        called_url = mock_post.call_args[0][0]
        assert "setplaylist/player123/pepsi_spring26_deposit" in called_url

    @patch("requests.post")
    def test_invalid_override_falls_back_to_default(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        connected_adapter.show_deposit_item(playlist_override="../bad")
        called_url = mock_post.call_args[0][0]
        assert "setplaylist/player123/tsv6_processing" in called_url
```

Also add a config-default test at the end of `TestPiSignageAdapterConnect` (or a new class):

```python
class TestPiSignageConfigDefaults:
    def test_deposit_default_is_processing_playlist(self):
        from tsv6.display.pisignage_adapter import PiSignageConfig
        assert PiSignageConfig().deposit_playlist == "tsv6_processing"
```

- [ ] **Step 2.2: Run tests to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_pisignage_adapter.py::TestPiSignageAdapterDepositOverride tests/unit/test_pisignage_adapter.py::TestPiSignageConfigDefaults -v`
Expected: tests fail. The override tests fail with `TypeError: show_deposit_item() got an unexpected keyword argument 'playlist_override'`. The config-default test fails because the default is currently `"tsv6_deposit_item"`.

- [ ] **Step 2.3: Implement**

In `src/tsv6/display/pisignage_adapter.py`:

Change line 50 (the `deposit_playlist` field) from:
```python
deposit_playlist: str = "tsv6_deposit_item"
```
to:
```python
deposit_playlist: str = "tsv6_processing"
```

Find `def show_deposit_item(self) -> bool:` (around line 245) and replace the entire method with:

```python
def show_deposit_item(self, playlist_override: str | None = None) -> bool:
    """Switch to the 'Please Deposit Your Item' screen.

    Args:
        playlist_override: Optional AWS-supplied playlist name for per-campaign
            messaging during the deposit stage. Falls back to
            ``self._config.deposit_playlist`` when absent or invalid.
    """
    name = self._resolve_playlist(playlist_override, self._config.deposit_playlist)
    return self.switch_playlist(name)
```

- [ ] **Step 2.4: Run tests**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_pisignage_adapter.py -v`
Expected: all tests pass (including the new and the existing).

- [ ] **Step 2.5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/display/pisignage_adapter.py tests/unit/test_pisignage_adapter.py
git commit -m "feat(pisignage): show_deposit_item accepts playlist_override; default is tsv6_processing"
```

---

## Task 3: `show_product_display` override

**Files:**
- Modify: `src/tsv6/display/pisignage_adapter.py`
- Test: `tests/unit/test_pisignage_adapter.py`

- [ ] **Step 3.1: Write failing tests**

Append to `tests/unit/test_pisignage_adapter.py`:

```python
class TestPiSignageAdapterProductOverride:
    """show_product_display respects the optional playlist_override kwarg."""

    @patch("requests.post")
    def test_default_uses_tsv6_product_display(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        connected_adapter.show_product_display()
        called_url = mock_post.call_args[0][0]
        assert "setplaylist/player123/tsv6_product_display" in called_url

    @patch("requests.post")
    def test_override_takes_precedence(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        connected_adapter.show_product_display(playlist_override="pepsi_spring26_reward")
        called_url = mock_post.call_args[0][0]
        assert "setplaylist/player123/pepsi_spring26_reward" in called_url

    @patch("requests.post")
    def test_legacy_args_accepted_but_ignored(self, mock_post, connected_adapter):
        """product_image_path / qr_url / nfc_url remain in signature for native-backend parity."""
        mock_post.return_value = _mock_response({"success": True})
        connected_adapter.show_product_display(
            product_image_path="/tmp/x.jpg",
            qr_url="https://example.com/r/1",
            nfc_url="https://example.com/r/1",
            playlist_override="x_campaign",
        )
        called_url = mock_post.call_args[0][0]
        assert "setplaylist/player123/x_campaign" in called_url

    @patch("requests.post")
    def test_invalid_override_falls_back(self, mock_post, connected_adapter):
        mock_post.return_value = _mock_response({"success": True})
        connected_adapter.show_product_display(playlist_override="bad name with space")
        called_url = mock_post.call_args[0][0]
        assert "setplaylist/player123/tsv6_product_display" in called_url
```

- [ ] **Step 3.2: Run tests to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_pisignage_adapter.py::TestPiSignageAdapterProductOverride -v`
Expected: tests fail with `TypeError: show_product_display() got an unexpected keyword argument 'playlist_override'`.

- [ ] **Step 3.3: Implement**

In `src/tsv6/display/pisignage_adapter.py`, find the existing `def show_product_display(...)` (around line 253) and replace its signature + body. Keep the legacy `product_image_path`, `qr_url`, `nfc_url` arguments so the native backend stays compatible — they remain ignored by this REST adapter:

```python
def show_product_display(
    self,
    product_image_path: str = "",
    qr_url: str = "",
    nfc_url: str | None = None,
    playlist_override: str | None = None,
) -> bool:
    """Switch to the product result playlist.

    Args:
        product_image_path: Reserved for native-backend renderers; ignored here.
        qr_url: Reserved for native-backend renderers; ignored here. The QR is
            rendered Pi-side by ``QrOverlay`` when this adapter is the active
            display backend.
        nfc_url: Reserved for native-backend renderers; ignored here.
        playlist_override: Optional AWS-supplied playlist name for per-campaign
            reward content. Falls back to ``self._config.product_playlist`` when
            absent or invalid.
    """
    name = self._resolve_playlist(playlist_override, self._config.product_playlist)
    return self.switch_playlist(name)
```

- [ ] **Step 3.4: Run tests**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_pisignage_adapter.py -v`
Expected: all pass.

- [ ] **Step 3.5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/display/pisignage_adapter.py tests/unit/test_pisignage_adapter.py
git commit -m "feat(pisignage): show_product_display accepts playlist_override"
```

---

## Task 4: DisplayController Protocol + TSV6NativeBackend signature parity

**Files:**
- Modify: `src/tsv6/display/controller.py`
- Modify: `src/tsv6/display/tsv6_player/backend.py`
- Test: `tests/unit/test_native_backend.py` (light addition)

- [ ] **Step 4.1: Write failing test**

Append to `tests/unit/test_native_backend.py` (use `inspect` so we test the signature, not the runtime behaviour — native backend doesn't actually do anything with override):

```python
def test_native_backend_show_methods_accept_playlist_override():
    import inspect
    from tsv6.display.tsv6_player.backend import TSV6NativeBackend

    deposit_sig = inspect.signature(TSV6NativeBackend.show_deposit_item)
    assert "playlist_override" in deposit_sig.parameters

    product_sig = inspect.signature(TSV6NativeBackend.show_product_display)
    assert "playlist_override" in product_sig.parameters
```

- [ ] **Step 4.2: Run test to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_native_backend.py::test_native_backend_show_methods_accept_playlist_override -v`
Expected: fails — `playlist_override` not in signature.

- [ ] **Step 4.3: Update Protocol in `controller.py`**

In `src/tsv6/display/controller.py`, replace the `show_deposit_item` and `show_product_display` Protocol methods (around lines 80–99):

```python
def show_deposit_item(self, playlist_override: str | None = None) -> bool:
    """Switch to the 'Please Deposit Your Item' screen.

    Args:
        playlist_override: Optional per-campaign playlist name. Backends that
            don't support per-call playlist switching should ignore this.
    """
    ...

def show_product_display(
    self,
    product_image_path: str = "",
    qr_url: str = "",
    nfc_url: str | None = None,
    playlist_override: str | None = None,
) -> bool:
    """Switch to the product result screen.

    Args:
        product_image_path: Filesystem path to the product image asset
            (used by native backend; ignored by REST adapter).
        qr_url: URL to encode in the on-screen QR code (used by native backend;
            REST adapter renders QR via the Pi-side ``QrOverlay`` instead).
        nfc_url: Optional URL to broadcast via NFC (None = omit; ignored on
            paths that don't broadcast NFC).
        playlist_override: Optional per-campaign playlist name.
    Returns True on success.
    """
    ...
```

- [ ] **Step 4.4: Update `TSV6NativeBackend` in `backend.py`**

In `src/tsv6/display/tsv6_player/backend.py`, edit the two methods (around lines 355 and 362):

Replace `def show_deposit_item(self) -> bool:` with:

```python
def show_deposit_item(self, playlist_override: str | None = None) -> bool:
    """Switch to the 'Please Deposit Your Item' screen.

    ``playlist_override`` accepted for ``DisplayController`` parity; the native
    renderer has no per-call playlist switch concept, so it is ignored.
    """
    self._interrupt_current_idle()
    if self._renderer is None:
        return False
    return self._renderer.show_deposit_item()
```

Replace the `def show_product_display(...)` signature (around line 362). Keep all existing args, add `playlist_override`:

```python
def show_product_display(
    self,
    product_image_path: str,
    qr_url: str,
    nfc_url: str | None = None,
    playlist_override: str | None = None,
) -> bool:
    """
    Switch to the product result screen.

    ``playlist_override`` accepted for ``DisplayController`` parity; ignored by
    the native renderer.
    """
    self._interrupt_current_idle()
    if self._renderer is None:
        return False
    return self._renderer.show_product_display(
        image_path=Path(product_image_path),
        qr_url=qr_url,
        nfc_url=nfc_url,
    )
```

- [ ] **Step 4.5: Run tests**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_native_backend.py tests/unit/test_pisignage_adapter.py tests/unit/test_player_protocol.py -v`
Expected: all pass.

- [ ] **Step 4.6: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/display/controller.py src/tsv6/display/tsv6_player/backend.py tests/unit/test_native_backend.py
git commit -m "feat(display): add playlist_override to DisplayController Protocol + native backend"
```

---

## Task 5: `QrOverlay` module

**Files:**
- Create: `src/tsv6/display/qr_overlay.py`
- Create: `tests/unit/test_qr_overlay.py`

- [ ] **Step 5.1: Write failing tests**

Create `tests/unit/test_qr_overlay.py`:

```python
"""Unit tests for the Pi-side QR overlay window."""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def overlay():
    from tsv6.display.qr_overlay import QrOverlay
    return QrOverlay(size_px=180, position="bottom-right")


class TestQrOverlayShowHide:
    def test_show_with_empty_url_is_noop(self, overlay):
        # No exception, no QR generation, no Tk window.
        overlay.show("", duration_sec=1.0)
        # _active is the internal "currently shown" sentinel.
        assert overlay.is_visible() is False

    def test_show_then_hide_idempotent(self, overlay):
        with patch("tsv6.display.qr_overlay.generate_qr_code") as gen:
            gen.return_value = MagicMock()  # PIL Image
            with patch.object(overlay, "_render_window", return_value=None) as render:
                overlay.show("https://example.com/r/abc", duration_sec=1.0)
                assert overlay.is_visible() is True
                render.assert_called_once()
        overlay.hide()
        assert overlay.is_visible() is False
        overlay.hide()  # second hide is a no-op

    def test_show_twice_replaces_prior(self, overlay):
        with patch("tsv6.display.qr_overlay.generate_qr_code") as gen:
            gen.return_value = MagicMock()
            with patch.object(overlay, "_render_window", return_value=None):
                overlay.show("https://example.com/r/1", duration_sec=10.0)
                first_token = overlay._show_token
                overlay.show("https://example.com/r/2", duration_sec=10.0)
                second_token = overlay._show_token
                assert second_token != first_token

    def test_show_with_no_display_env_is_graceful(self, overlay, monkeypatch, caplog):
        monkeypatch.delenv("DISPLAY", raising=False)
        with patch(
            "tsv6.display.qr_overlay.generate_qr_code",
            side_effect=RuntimeError("no display"),
        ):
            with caplog.at_level("WARNING"):
                overlay.show("https://example.com/r/1", duration_sec=1.0)
        # Does not raise; logs a warning.
        assert overlay.is_visible() is False
        assert any("qr overlay" in r.message.lower() for r in caplog.records)

    def test_qr_generator_called_with_url(self, overlay):
        with patch("tsv6.display.qr_overlay.generate_qr_code") as gen:
            gen.return_value = MagicMock()
            with patch.object(overlay, "_render_window", return_value=None):
                overlay.show("https://example.com/abc", duration_sec=1.0)
        assert gen.called
        args, kwargs = gen.call_args
        assert args[0] == "https://example.com/abc"

    def test_shutdown_clears_state(self, overlay):
        with patch("tsv6.display.qr_overlay.generate_qr_code") as gen:
            gen.return_value = MagicMock()
            with patch.object(overlay, "_render_window", return_value=None):
                overlay.show("https://example.com/r/1", duration_sec=10.0)
        overlay.shutdown()
        assert overlay.is_visible() is False
```

- [ ] **Step 5.2: Run tests to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_qr_overlay.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'tsv6.display.qr_overlay'`.

- [ ] **Step 5.3: Implement `QrOverlay`**

Create `src/tsv6/display/qr_overlay.py`:

```python
"""Always-on-top QR overlay window for the PiSignage product display stage.

The PiSignage Chromium kiosk owns the full screen. This module pops a small
borderless Tk ``Toplevel`` over it for the duration of the product playlist,
displaying the per-transaction QR code, then hides it.

The Tk root runs on a private daemon thread because the main process has no Tk
mainloop in PiSignage mode.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from tsv6.utils.qr_generator import generate_qr_code

logger = logging.getLogger(__name__)

_VALID_POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left", "center")


class QrOverlay:
    """Renders a QR code over the PiSignage kiosk for a bounded duration."""

    def __init__(self, size_px: int = 220, position: str = "bottom-right"):
        if position not in _VALID_POSITIONS:
            logger.warning(
                "qr overlay: invalid position %r; using bottom-right", position
            )
            position = "bottom-right"
        self._size_px = max(80, int(size_px))
        self._position = position
        self._lock = threading.Lock()
        self._show_token = 0
        self._active = False
        self._tk_root = None
        self._tk_window = None
        self._hide_timer: Optional[threading.Timer] = None

    # ── Public API ───────────────────────────────────────────────────────

    def show(self, url: str, duration_sec: float) -> None:
        """Show the QR code for ``url`` for ``duration_sec`` seconds. Idempotent."""
        if not url:
            return
        with self._lock:
            self._show_token += 1
            token = self._show_token
            self._cancel_hide_timer_locked()
            try:
                qr_img = generate_qr_code(url, size=self._size_px)
            except Exception as e:
                logger.warning("qr overlay: QR generation failed: %s", e)
                return
            try:
                self._render_window(qr_img)
                self._active = True
            except Exception as e:
                logger.warning("qr overlay: render failed: %s", e)
                self._active = False
                return
            t = threading.Timer(duration_sec, self._auto_hide, args=(token,))
            t.daemon = True
            t.start()
            self._hide_timer = t

    def hide(self) -> None:
        """Hide the overlay if shown. Idempotent."""
        with self._lock:
            self._cancel_hide_timer_locked()
            self._destroy_window_locked()
            self._active = False

    def shutdown(self) -> None:
        """Tear down for process exit."""
        self.hide()

    def is_visible(self) -> bool:
        return self._active

    # ── Internals ────────────────────────────────────────────────────────

    def _cancel_hide_timer_locked(self) -> None:
        t, self._hide_timer = self._hide_timer, None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    def _auto_hide(self, token: int) -> None:
        with self._lock:
            if token != self._show_token:
                # Superseded by a later show().
                return
        self.hide()

    def _render_window(self, qr_img) -> None:
        """Lazily create the Tk root and a borderless Toplevel; place by position.

        Tk is created on first use because PiSignage mode has no Tk root. If the
        DISPLAY isn't available, ``tkinter.Tk()`` raises ``TclError`` — the
        caller catches the exception and logs.
        """
        import tkinter as tk
        from PIL import ImageTk

        if self._tk_root is None:
            self._tk_root = tk.Tk()
            self._tk_root.withdraw()  # hide the implicit root

        win = tk.Toplevel(self._tk_root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        photo = ImageTk.PhotoImage(qr_img, master=win)
        label = tk.Label(win, image=photo, bd=0)
        label.image = photo  # retain reference
        label.pack()
        self._tk_window = win
        # Geometry placement
        sw = self._tk_root.winfo_screenwidth()
        sh = self._tk_root.winfo_screenheight()
        s = self._size_px
        margin = 24
        positions = {
            "bottom-right": (sw - s - margin, sh - s - margin),
            "bottom-left": (margin, sh - s - margin),
            "top-right": (sw - s - margin, margin),
            "top-left": (margin, margin),
            "center": ((sw - s) // 2, (sh - s) // 2),
        }
        x, y = positions[self._position]
        win.geometry(f"{s}x{s}+{x}+{y}")
        win.update()

    def _destroy_window_locked(self) -> None:
        win, self._tk_window = self._tk_window, None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass


def from_env() -> "QrOverlay":
    """Construct a QrOverlay from ``TSV6_QR_OVERLAY_*`` env vars."""
    pos = os.environ.get("TSV6_QR_OVERLAY_POSITION", "bottom-right")
    try:
        size = int(os.environ.get("TSV6_QR_OVERLAY_SIZE_PX", "220"))
    except ValueError:
        size = 220
    return QrOverlay(size_px=size, position=pos)
```

- [ ] **Step 5.4: Run tests**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_qr_overlay.py -v`
Expected: all 6 tests pass. (Tests patch `_render_window` so no real Tk is required.)

- [ ] **Step 5.5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/display/qr_overlay.py tests/unit/test_qr_overlay.py
git commit -m "feat(display): add QrOverlay for Pi-side QR rendering over PiSignage kiosk"
```

---

## Task 6: Wire deposit override in `_on_product_image_display`

**Files:**
- Modify: `src/tsv6/core/production_main.py`
- Test: `tests/unit/test_production_main_pisignage_flow.py` (new)

- [ ] **Step 6.1: Create the new test file with one failing test**

Create `tests/unit/test_production_main_pisignage_flow.py`:

```python
"""Tests for the PiSignage scan-response flow on ProductionVideoPlayer.

These tests exercise only the new override + QR overlay + idle timer logic.
They construct ProductionVideoPlayer in a heavily mocked state so the rest of
the system (AWS, servo, recycle sensor, network) is not exercised.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def player():
    """A barely-initialised ProductionVideoPlayer with mocked collaborators."""
    from tsv6.core.production_main import ProductionVideoPlayer
    p = ProductionVideoPlayer.__new__(ProductionVideoPlayer)
    # Minimum fields the methods we test touch:
    p.logger = MagicMock()
    p.error_recovery = MagicMock()
    p.video_player = None
    p.servo_controller = None
    p.recycle_sensor = None
    p.display_backend = MagicMock()
    p.qr_overlay = MagicMock()
    p._idle_timer = None
    # _door_sequence_lock + flag (used by _on_product_image_display)
    import threading
    p._door_sequence_lock = threading.Lock()
    p._door_sequence_active = False
    return p


class TestDepositOverride:
    def test_deposit_playlist_override_passed_to_backend(self, player):
        payload = {
            "barcode": "076406668106",
            "depositPlaylist": "pepsi_spring26_deposit",
        }
        player._on_product_image_display(payload)
        player.display_backend.show_deposit_item.assert_called_once_with(
            playlist_override="pepsi_spring26_deposit"
        )

    def test_no_deposit_override_passes_none(self, player):
        payload = {"barcode": "076406668106"}
        player._on_product_image_display(payload)
        player.display_backend.show_deposit_item.assert_called_once_with(
            playlist_override=None
        )
```

- [ ] **Step 6.2: Run tests to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py::TestDepositOverride -v`
Expected: tests fail — `display_backend.show_deposit_item` is called without `playlist_override` kwarg.

- [ ] **Step 6.3: Modify `_on_product_image_display`**

In `src/tsv6/core/production_main.py`, find the call at line 1407 and change:

```python
            # Show deposit waiting screen (replaces processing image)
            if self.display_backend is not None:
                self.display_backend.show_deposit_item()
```

to:

```python
            # Show deposit waiting screen (replaces processing image)
            deposit_override = (
                product_data.get("depositPlaylist")
                if isinstance(product_data, dict)
                else None
            )
            if self.display_backend is not None:
                self.display_backend.show_deposit_item(playlist_override=deposit_override)
```

- [ ] **Step 6.4: Run tests**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py::TestDepositOverride -v`
Expected: pass.

- [ ] **Step 6.5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/core/production_main.py tests/unit/test_production_main_pisignage_flow.py
git commit -m "feat(production): pass depositPlaylist override from AWS payload to backend"
```

---

## Task 7: Idle timer + return-to-idle helpers

**Files:**
- Modify: `src/tsv6/core/production_main.py`
- Test: `tests/unit/test_production_main_pisignage_flow.py`

- [ ] **Step 7.1: Write failing tests**

Append to `tests/unit/test_production_main_pisignage_flow.py`:

```python
class TestIdleTimer:
    def test_arm_idle_timer_creates_threading_timer(self, player):
        player._arm_idle_timer(0.05)
        assert player._idle_timer is not None
        # Allow the timer to fire
        import time
        time.sleep(0.15)
        player.display_backend.show_idle.assert_called_once_with()
        player.qr_overlay.hide.assert_called_once_with()

    def test_arm_idle_timer_replaces_prior(self, player):
        # Arm a long timer
        player._arm_idle_timer(60.0)
        first = player._idle_timer
        # Re-arm; old timer should be cancelled
        player._arm_idle_timer(60.0)
        assert player._idle_timer is not first
        # Cancelled timer should not fire show_idle even after a sleep
        import time
        first_was_alive_before_cancel = True  # we re-armed, original is cancelled
        # Tear down second timer too
        player._idle_timer.cancel()

    def test_return_to_idle_swallows_backend_exception(self, player):
        player.display_backend.show_idle.side_effect = RuntimeError("boom")
        # Should not raise.
        player._return_to_idle()
        player.qr_overlay.hide.assert_called_once_with()
        player.logger.exception.assert_called_once()
```

- [ ] **Step 7.2: Run tests to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py::TestIdleTimer -v`
Expected: fails — `_arm_idle_timer` and `_return_to_idle` don't exist.

- [ ] **Step 7.3: Add helpers to `ProductionVideoPlayer`**

In `src/tsv6/core/production_main.py`, add these methods to the `ProductionVideoPlayer` class. Place them adjacent to `_handle_recycle_success` (near line 1590, just before `_handle_recycle_failure`):

```python
def _arm_idle_timer(self, seconds: float) -> None:
    """Arm a one-shot timer that returns the kiosk to the idle playlist.

    Re-arming cancels any prior pending idle return.
    """
    import threading as _threading
    if self._idle_timer is not None:
        try:
            self._idle_timer.cancel()
        except Exception:
            pass
    t = _threading.Timer(seconds, self._return_to_idle)
    t.daemon = True
    self._idle_timer = t
    t.start()

def _return_to_idle(self) -> None:
    """Hide the QR overlay and switch the kiosk back to ``tsv6_idle_loop``."""
    try:
        if getattr(self, "qr_overlay", None) is not None:
            self.qr_overlay.hide()
        if self.display_backend is not None:
            self.display_backend.show_idle()
    except Exception:
        self.logger.exception("idle return failed")
```

In `ProductionVideoPlayer.__init__`, add `self._idle_timer = None` near the other instance-attribute initialisations (alongside the existing `self._door_sequence_active = False` near `__init__`):

Find a line in `__init__` that sets `self._door_sequence_active = False` (or the closest similar init). Right after it add:

```python
        self._idle_timer = None
        self.qr_overlay = None  # set by _initialize_pisignage when REST backend is active
```

- [ ] **Step 7.4: Run tests**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py::TestIdleTimer -v`
Expected: pass.

- [ ] **Step 7.5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/core/production_main.py tests/unit/test_production_main_pisignage_flow.py
git commit -m "feat(production): add _arm_idle_timer and _return_to_idle helpers"
```

---

## Task 8: Wire product override + QR overlay in `_handle_recycle_success`

**Files:**
- Modify: `src/tsv6/core/production_main.py`
- Test: `tests/unit/test_production_main_pisignage_flow.py`

- [ ] **Step 8.1: Write failing tests**

Append to `tests/unit/test_production_main_pisignage_flow.py`:

```python
class TestProductOverrideAndOverlay:
    def _payload(self, **extra):
        base = {
            "barcode": "076406668106",
            "imageUrl": "https://example.com/p.jpg",
            "qrUrl": "https://example.com/r/abc",
        }
        base.update(extra)
        return base

    def test_product_override_passed_to_backend(self, player):
        player._publish_recycle_result = MagicMock()
        player._handle_recycle_success(
            product_data=self._payload(productPlaylist="pepsi_spring26_reward"),
            nfc_url="",
            transaction_id="tx-1",
        )
        player.display_backend.show_product_display.assert_called_once()
        kwargs = player.display_backend.show_product_display.call_args.kwargs
        assert kwargs["playlist_override"] == "pepsi_spring26_reward"

    def test_product_no_override_passes_none(self, player):
        player._publish_recycle_result = MagicMock()
        player._handle_recycle_success(
            product_data=self._payload(),
            nfc_url="",
            transaction_id="tx-1",
        )
        kwargs = player.display_backend.show_product_display.call_args.kwargs
        assert kwargs["playlist_override"] is None

    def test_qr_overlay_shown_with_qr_url(self, player):
        player._publish_recycle_result = MagicMock()
        player._handle_recycle_success(
            product_data=self._payload(),
            nfc_url="",
            transaction_id="tx-1",
        )
        player.qr_overlay.show.assert_called_once()
        args, kwargs = player.qr_overlay.show.call_args
        assert args[0] == "https://example.com/r/abc"
        # duration default 8s
        assert kwargs.get("duration_sec", args[1] if len(args) > 1 else None) == 8.0

    def test_idle_timer_armed(self, player):
        player._publish_recycle_result = MagicMock()
        with patch.object(player, "_arm_idle_timer") as arm:
            player._handle_recycle_success(
                product_data=self._payload(),
                nfc_url="",
                transaction_id="tx-1",
            )
        arm.assert_called_once_with(8.0)

    def test_recycle_result_published(self, player):
        player._publish_recycle_result = MagicMock()
        player._handle_recycle_success(
            product_data=self._payload(),
            nfc_url="",
            transaction_id="tx-1",
        )
        player._publish_recycle_result.assert_called_once_with(
            barcode="076406668106",
            transaction_id="tx-1",
            status="recycle_success",
        )
```

- [ ] **Step 8.2: Run tests to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py::TestProductOverrideAndOverlay -v`
Expected: fails — current `show_product_display` call uses `product_image_path=...` positional kwargs, no `playlist_override`, no QR overlay, no idle timer.

- [ ] **Step 8.3: Modify `_handle_recycle_success`**

In `src/tsv6/core/production_main.py`, locate `_handle_recycle_success` (around line 1550). Replace the body of the `if self.display_backend is not None:` block (around lines 1561–1576). Old:

```python
        # Show product image + QR code on whichever display backend is active
        product_image_path = product_data.get('imageUrl', '')
        qr_url = product_data.get('qrUrl', product_data.get('nfcUrl', ''))

        if self.display_backend is not None:
            self.display_backend.show_product_display(
                product_image_path=product_image_path,
                qr_url=qr_url,
                nfc_url=nfc_url or None,
            )
        elif self.video_player:
            if hasattr(self.video_player, 'hide_deposit_waiting'):
                self.video_player.hide_deposit_waiting()
            if hasattr(self.video_player, 'display_product_image'):
                self.video_player.display_product_image(product_data)
```

New:

```python
        # Show product image + QR code on whichever display backend is active
        product_image_path = product_data.get('imageUrl', '')
        qr_url = product_data.get('qrUrl', product_data.get('nfcUrl', ''))
        product_override = (
            product_data.get('productPlaylist')
            if isinstance(product_data, dict)
            else None
        )

        if self.display_backend is not None:
            self.display_backend.show_product_display(
                product_image_path=product_image_path,
                qr_url=qr_url,
                nfc_url=nfc_url or None,
                playlist_override=product_override,
            )
            # Pi-side QR overlay over the PiSignage kiosk for the product window.
            if self.qr_overlay is not None and qr_url:
                try:
                    self.qr_overlay.show(qr_url, duration_sec=8.0)
                except Exception:
                    self.logger.exception("qr overlay show failed")
            self._arm_idle_timer(8.0)
        elif self.video_player:
            if hasattr(self.video_player, 'hide_deposit_waiting'):
                self.video_player.hide_deposit_waiting()
            if hasattr(self.video_player, 'display_product_image'):
                self.video_player.display_product_image(product_data)
```

- [ ] **Step 8.4: Run tests**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py -v`
Expected: pass.

- [ ] **Step 8.5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/core/production_main.py tests/unit/test_production_main_pisignage_flow.py
git commit -m "feat(production): wire productPlaylist override + QR overlay + idle timer"
```

---

## Task 9: Cancel timer + hide overlay on a new scan within the window

**Files:**
- Modify: `src/tsv6/core/production_main.py`
- Test: `tests/unit/test_production_main_pisignage_flow.py`

- [ ] **Step 9.1: Write failing test**

Append to `tests/unit/test_production_main_pisignage_flow.py`:

```python
class TestNewScanCancelsIdleWindow:
    def test_new_scan_cancels_pending_timer_and_hides_overlay(self, player):
        # Pretend a previous success armed the timer + overlay
        import threading as _threading
        timer = _threading.Timer(60.0, lambda: None)
        timer.daemon = True
        timer.start()
        player._idle_timer = timer
        player._on_barcode_scanned("076406668106", "tx-2")
        assert player._idle_timer is None or not player._idle_timer.is_alive()
        player.qr_overlay.hide.assert_called_once_with()
        player.display_backend.show_processing.assert_called_once_with()
```

- [ ] **Step 9.2: Run test to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py::TestNewScanCancelsIdleWindow -v`
Expected: fails — `_on_barcode_scanned` doesn't cancel the timer or hide the overlay.

- [ ] **Step 9.3: Modify `_on_barcode_scanned`**

In `src/tsv6/core/production_main.py`, locate `_on_barcode_scanned` (around line 1784). Replace its body's first action with the cancellation block. Old beginning:

```python
    def _on_barcode_scanned(self, barcode_data, transaction_id):
        """Handle barcode scan events"""
        try:
            self.logger.info(f"Barcode scanned: {barcode_data}")

            # Show processing screen while awaiting AWS response
            if self.display_backend is not None:
                self.display_backend.show_processing()
            elif self.video_player and hasattr(self.video_player, 'next_video'):
```

New:

```python
    def _on_barcode_scanned(self, barcode_data, transaction_id):
        """Handle barcode scan events"""
        try:
            self.logger.info(f"Barcode scanned: {barcode_data}")

            # Cancel any pending product-stage idle return + hide QR overlay,
            # so a rapid second scan immediately resets the display state.
            if getattr(self, "_idle_timer", None) is not None:
                try:
                    self._idle_timer.cancel()
                except Exception:
                    pass
                self._idle_timer = None
            if getattr(self, "qr_overlay", None) is not None:
                try:
                    self.qr_overlay.hide()
                except Exception:
                    self.logger.exception("qr overlay hide failed")

            # Show processing screen while awaiting AWS response
            if self.display_backend is not None:
                self.display_backend.show_processing()
            elif self.video_player and hasattr(self.video_player, 'next_video'):
```

- [ ] **Step 9.4: Run tests**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py -v`
Expected: all pass.

- [ ] **Step 9.5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/core/production_main.py tests/unit/test_production_main_pisignage_flow.py
git commit -m "feat(production): cancel idle timer + hide QR overlay on new scan"
```

---

## Task 10: Instantiate `QrOverlay` in `_initialize_pisignage` and tear down on shutdown

**Files:**
- Modify: `src/tsv6/core/production_main.py`

- [ ] **Step 10.1: Add a wiring test**

Append to `tests/unit/test_production_main_pisignage_flow.py`:

```python
class TestQrOverlayLifecycleWiring:
    def test_initialize_pisignage_assigns_qr_overlay(self):
        """When PiSignage REST backend initializes successfully, qr_overlay is set."""
        from tsv6.core.production_main import ProductionVideoPlayer
        # Sanity: confirm the source uses QrOverlay.from_env or QrOverlay(...) inside _initialize_pisignage.
        import inspect
        src = inspect.getsource(ProductionVideoPlayer._initialize_pisignage)
        assert "QrOverlay" in src
```

- [ ] **Step 10.2: Run test to verify failure**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py::TestQrOverlayLifecycleWiring -v`
Expected: fails — `QrOverlay` not yet referenced in `_initialize_pisignage`.

- [ ] **Step 10.3: Wire `QrOverlay` into `_initialize_pisignage`**

In `src/tsv6/core/production_main.py`:

Add a top-of-file import alongside other display imports:

```python
from tsv6.display.qr_overlay import QrOverlay
```

In `_initialize_pisignage` (around line 838), at the point where `self.display_backend = self.pisignage_adapter` is set (search for that exact assignment), add immediately after it:

```python
            # QR overlay rides on top of the PiSignage kiosk during the product stage.
            try:
                self.qr_overlay = QrOverlay(
                    size_px=int(os.environ.get("TSV6_QR_OVERLAY_SIZE_PX", "220")),
                    position=os.environ.get("TSV6_QR_OVERLAY_POSITION", "bottom-right"),
                )
            except Exception:
                self.logger.exception("qr overlay init failed; continuing without overlay")
                self.qr_overlay = None
```

In `ProductionVideoPlayer.shutdown` (locate the existing `def shutdown` method), add — only if not already there — a clean teardown call near the start of the method body, after any existing logging:

```python
        try:
            if getattr(self, "qr_overlay", None) is not None:
                self.qr_overlay.shutdown()
        except Exception:
            self.logger.exception("qr overlay shutdown failed")
```

- [ ] **Step 10.4: Run tests**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit/test_production_main_pisignage_flow.py -v`
Expected: pass.

- [ ] **Step 10.5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add src/tsv6/core/production_main.py tests/unit/test_production_main_pisignage_flow.py
git commit -m "feat(production): instantiate QrOverlay in _initialize_pisignage; tear down on shutdown"
```

---

## Task 11: Document the new env vars in `tsv6.service`

**Files:**
- Modify: `tsv6.service`

- [ ] **Step 11.1: Read the current service file**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && grep -n -E '^Environment=' tsv6.service | head -30`
Expected: a list of `Environment="..."` entries; locate the PiSignage block (around lines 109–135).

- [ ] **Step 11.2: Append new env-var lines**

In `tsv6.service`, immediately after the last `Environment="PISIGNAGE_..."` line, add:

```
Environment="TSV6_QR_OVERLAY_POSITION=bottom-right"
Environment="TSV6_QR_OVERLAY_SIZE_PX=220"
Environment="TSV6_PRODUCT_PLAYLIST_DURATION_SEC=8"
```

(The `TSV6_PRODUCT_PLAYLIST_DURATION_SEC` is reserved for follow-up — current implementation hard-codes 8.0 — but documenting it now makes the intent visible to ops.)

- [ ] **Step 11.3: Verify no syntax issues**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && grep -n 'TSV6_QR_OVERLAY\|TSV6_PRODUCT_PLAYLIST_DURATION' tsv6.service`
Expected: 3 lines printed.

- [ ] **Step 11.4: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add tsv6.service
git commit -m "chore(service): document QR overlay + product playlist duration env vars"
```

---

## Task 12: Full unit-test run + lint sanity

- [ ] **Step 12.1: Full unit-test pass**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run pytest tests/unit -q`
Expected: all tests pass. If pre-existing failures appear that are unrelated to this work, note them but do not fix here.

- [ ] **Step 12.2: Module imports cleanly**

Run: `cd /home/g1tech/tsrpi7/tsrpi5 && uv run python -c "import tsv6.display.qr_overlay; import tsv6.display.pisignage_adapter; import tsv6.core.production_main; print('ok')"`
Expected: `ok`.

- [ ] **Step 12.3: No commit (verification only).**

---

## Task 13: Manual on-device integration test

This task is **manual** — run it on the actual Pi with the scanner attached. No commits are produced; outcome is recorded as a checklist below.

**Pre-conditions:**
- Scanner connected (`/dev/input/event6` HID-keyboard mode) — already verified.
- `PISIGNAGE_ENABLED=true` in `tsv6.service`.
- TS Media server (`tsmedia.g1tech.cloud`) reachable; standard playlists `tsv6_idle_loop`, `tsv6_processing`, `tsv6_product_display`, `tsv6_no_match`, `tsv6_no_item_detected` exist.
- Backend (AWS lambda) **may or may not** yet send the new fields — both cases are exercised below.

- [ ] **Step 13.1: Restart the service**

```bash
sudo systemctl restart tsv6.service
journalctl -u tsv6.service -f
```
Watch logs until you see `✓ PiSignage adapter ready` (or equivalent) and the kiosk shows `tsv6_idle_loop`.

- [ ] **Step 13.2: Default-flow scan (no AWS overrides)**

Scan a known-good barcode whose AWS response **omits** `depositPlaylist` and `productPlaylist`. Watch the kiosk:
1. Goes to `tsv6_processing` (after scan, while AWS round-trip).
2. AWS replies `openDoor` → still `tsv6_processing` (deposit-stage default is now also `tsv6_processing`).
3. Servo opens → drop the test item.
4. Sensor fires → kiosk switches to `tsv6_product_display`. The Pi-side QR overlay appears bottom-right.
5. ~8 s later → kiosk returns to `tsv6_idle_loop`, QR overlay disappears.

Verify in `journalctl` for the `scan_response` audit log line with `deposit_override=False product_override=False`.

- [ ] **Step 13.3: Override-flow scan (AWS sends both fields)**

Either modify the AWS lambda response or use a debug script to inject a synthetic `openDoor` payload with `depositPlaylist=` and `productPlaylist=` set to real playlist names on TS Media. Confirm the kiosk plays those overrides.

- [ ] **Step 13.4: Bogus-name resilience**

Inject a payload with `productPlaylist="this_does_not_exist"`. Confirm the device logs `WARNING ... falling back ...`, plays `tsv6_product_display` instead, and the QR overlay still appears for 8 s.

- [ ] **Step 13.5: Rapid double-scan**

Within the 8-second product window, scan a second barcode. Confirm:
- QR overlay disappears immediately.
- Kiosk cuts to `tsv6_processing`.
- The earlier idle return does **not** fire.

- [ ] **Step 13.6: VLC regression check**

Set `PISIGNAGE_ENABLED=false` in `tsv6.service`, restart. Scan a barcode. Confirm the original VLC overlay path (`_show_image_overlay`, NFC, etc.) still works.

- [ ] **Step 13.7: Record results**

Write each step's result (`PASS` / `FAIL` + notes) into a fresh file `docs/superpowers/runs/2026-04-24-pisignage-playlist-trigger-manual-test.md` and commit:

```bash
cd /home/g1tech/tsrpi7/tsrpi5
mkdir -p docs/superpowers/runs
# (write the file)
git add docs/superpowers/runs/2026-04-24-pisignage-playlist-trigger-manual-test.md
git commit -m "test(integration): record on-device PiSignage playlist trigger results"
```

---

## Done

After Task 13, the feature is live behind `PISIGNAGE_ENABLED=true`. Backend dependency: the AWS lambda must populate `depositPlaylist` and/or `productPlaylist` to actually exercise the override path. Until then, the device transparently falls back to defaults.

## Deferred from spec

- **Per-scan audit log line** ("INFO scan_response transaction=… deposit_playlist=… product_playlist=… deposit_override=… product_override=…"). The existing `_resolve_playlist` WARNINGs cover the main forensic case (invalid name fell back). Adding a single combined audit line would require threading the resolved deposit-stage playlist name from `_on_product_image_display` through `_verified_door_sequence` into `_handle_recycle_success`. Defer until ops actually need that audit shape — easy to add later without redesign.
