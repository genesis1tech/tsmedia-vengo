"""
Abstract DisplayController Protocol for TSV6 display backends.

Defines the interface that any display backend must implement, allowing
production code to depend on an abstract contract rather than a concrete
implementation. Backends include PiSignageAdapter (playlist switching via
REST) and any future local VLC or HTML-based renderer.

Usage::

    from tsv6.display.controller import DisplayController

    def show_state(display: DisplayController, state: str) -> None:
        if state == "idle":
            display.show_idle()
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class DisplayController(Protocol):
    """
    Protocol satisfied by any class that can drive the kiosk display.

    All ``show_*`` methods return ``True`` on success and ``False`` on
    failure (e.g. the backend is disconnected, the request timed out).
    ``disconnect``, ``start``, and ``stop`` return ``None``; callers that
    need to know whether lifecycle teardown succeeded should check
    ``is_connected`` afterwards.
    """

    # ── Lifecycle ────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Establish connection to the display backend.

        Returns True when the backend is ready to receive show_* calls.
        """
        ...

    def disconnect(self) -> None:
        """Tear down the connection gracefully."""
        ...

    def start(self) -> None:
        """Start background services (health monitor, polling loops, etc.)."""
        ...

    def stop(self) -> None:
        """Stop background services and release resources."""
        ...

    # ── State Query ──────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """True when the display backend is healthy and ready."""
        ...

    def get_metrics(self) -> dict:
        """Return backend telemetry for AWS status publish.

        Keys and values are implementation-defined; callers must not
        assume specific key names beyond the fact that the result is a
        plain ``dict`` safe to JSON-serialise.
        """
        ...

    # ── Display States ───────────────────────────────────────────────────

    def show_idle(self) -> bool:
        """Switch to the default looping state (attract loop / screensaver)."""
        ...

    def show_processing(self) -> bool:
        """Switch to the 'Verifying...' screen while awaiting server response."""
        ...

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

    def show_no_match(self) -> bool:
        """Switch to the 'Unrecognized Barcode' / cannot-accept screen."""
        ...

    def show_barcode_not_qr(self) -> bool:
        """Switch to the 'QR Code Detected — Use Barcode' error screen."""
        ...

    def show_no_item_detected(self) -> bool:
        """Switch to the 'Item Not Detected' screen (door opened, ToF miss)."""
        ...

    def show_offline(self) -> bool:
        """Switch to the offline / server-unreachable fallback screen."""
        ...
