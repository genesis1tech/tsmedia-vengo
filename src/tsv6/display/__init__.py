"""
PiSignage display integration for TSV6.

Replaces VLC-based local playback with PiSignage-mediated remote playback.
The PiSignage server runs on Hostinger VPS; the player runs on the Pi.
TSV6 drives playlist switching via the PiSignage REST API.
"""

from tsv6.display.pisignage_adapter import PiSignageAdapter, PiSignageConfig

__all__ = ["PiSignageAdapter", "PiSignageConfig"]
