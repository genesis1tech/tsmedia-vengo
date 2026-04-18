"""
tsv6.ads — Ad player package for Topper Stopper devices.

Orchestrates ad-pod fetching, local asset caching, VLC splice playback,
and signed proof-of-play reporting with an offline SQLite queue.

Primary entry point: AdPlayer
"""

from tsv6.ads.player import AdPlayer

__all__ = ["AdPlayer"]
