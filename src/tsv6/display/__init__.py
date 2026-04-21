"""
PiSignage display integration for TSV6.

Replaces VLC-based local playback with PiSignage-mediated remote playback.
The PiSignage server runs on Hostinger VPS; the player runs on the Pi.
TSV6 drives playlist switching via the PiSignage REST API.

Public API
----------
DisplayController
    Abstract Protocol that every display backend must satisfy.
PlayerIdentity
    Frozen dataclass holding CPU serial, device_id, player_name, and MACs.
get_player_identity
    Module-level factory that reads hardware identity from procfs/sysfs.
PiSignageAdapter
    Concrete DisplayController backed by the PiSignage REST API.
PiSignageConfig
    Frozen configuration dataclass for PiSignageAdapter.
"""

from tsv6.display.controller import DisplayController
from tsv6.display.identity import PlayerIdentity, get_player_identity
from tsv6.display.pisignage_adapter import PiSignageAdapter, PiSignageConfig

__all__ = [
    "DisplayController",
    "PlayerIdentity",
    "get_player_identity",
    "PiSignageAdapter",
    "PiSignageConfig",
]
