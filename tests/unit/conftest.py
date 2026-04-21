"""
Unit-test conftest for TSV6.

Pre-installs lightweight stubs into ``sys.modules`` before any test module is
collected.  Two stubs are needed:

1.  **vlc** — prevents the real ``python-vlc`` module from executing its
    module-level ``find_lib()`` call, which hangs on macOS and Linux CI
    machines that do not have VLC installed.

2.  **markupsafe._speedups** — on macOS 26 (Tahoe) and other environments
    where Gatekeeper rejects unsigned or unnotarised C extensions, the
    ``markupsafe._speedups.cpython-311-darwin.so`` binary is blocked by
    ``spctl``.  Python's ``dlopen()`` then hangs indefinitely waiting for
    notarisation rather than raising ``ImportError``.  The hang cascades:
    markupsafe → jinja2 → flask → RouterServer → test_renderer.py collection.

    The fix is to pre-register a fake ``markupsafe._speedups`` module that
    exposes ``_escape_inner`` backed by the pure-Python ``_native`` fallback.
    When ``markupsafe.__init__`` does ``from ._speedups import _escape_inner``
    it sees our stub and never attempts to ``dlopen`` the C extension.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# 1. markupsafe._speedups stub — must run BEFORE importing markupsafe
# ---------------------------------------------------------------------------


def _install_markupsafe_speedups_stub() -> None:
    """
    Register a pure-Python ``markupsafe._speedups`` stub.

    This is needed on macOS environments where the C extension
    ``_speedups.cpython-311-darwin.so`` is rejected by Gatekeeper (spctl),
    causing ``dlopen`` to hang rather than raise ``ImportError``.

    The stub re-uses the logic from ``markupsafe._native`` so the escape
    semantics are identical; only the C-accelerated path is bypassed.
    """
    if "markupsafe._speedups" in sys.modules:
        return  # Already loaded (real or stub).

    def _escape_inner(s: str, /) -> str:  # noqa: D401
        """Pure-Python HTML-safe escape (mirrors markupsafe._native)."""
        return (
            s.replace("&", "&amp;")
            .replace(">", "&gt;")
            .replace("<", "&lt;")
            .replace("'", "&#39;")
            .replace('"', "&#34;")
        )

    speedups_stub = types.ModuleType("markupsafe._speedups")
    speedups_stub._escape_inner = _escape_inner  # type: ignore[attr-defined]

    sys.modules["markupsafe._speedups"] = speedups_stub


# ---------------------------------------------------------------------------
# 2. vlc stub
# ---------------------------------------------------------------------------


def _install_vlc_stub() -> None:
    """Register a minimal ``vlc`` stub in sys.modules if VLC is not available."""
    if "vlc" in sys.modules:
        # Real VLC already loaded (unlikely in CI, but respect it).
        return

    stub = types.ModuleType("vlc")

    # Stub PlaybackMode enum-like object.
    _playback_mode = types.SimpleNamespace(loop=1, default=0, repeat=2)
    stub.PlaybackMode = _playback_mode  # type: ignore[attr-defined]

    # Stub Instance factory.
    stub.Instance = MagicMock  # type: ignore[attr-defined]

    # Stub top-level player classes used by legacy code.
    stub.MediaPlayer = MagicMock  # type: ignore[attr-defined]
    stub.MediaListPlayer = MagicMock  # type: ignore[attr-defined]

    # ``dll`` and ``plugin_path`` are referenced by some callers after import.
    stub.dll = None  # type: ignore[attr-defined]
    stub.plugin_path = None  # type: ignore[attr-defined]

    sys.modules["vlc"] = stub


# Install stubs as early as possible — before any test module is imported.
# Order matters: markupsafe must be stubbed before vlc (which may import flask
# via some code paths) and before any test that imports RouterServer.
_install_markupsafe_speedups_stub()
_install_vlc_stub()
