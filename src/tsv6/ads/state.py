"""
AdPlayerState enum — exposed to the AWS IoT shadow heartbeat.
"""

from enum import Enum


class AdPlayerState(Enum):
    """Lifecycle states of the ad player, published to the device shadow."""

    DISABLED = "disabled"
    """TSV6_AD_ENABLED is off; no ad activity."""

    IDLE = "idle"
    """No ad is playing; waiting for IDLE signal from recycling state machine."""

    PREFETCHING = "prefetching"
    """Fetching the next ad pod from the server."""

    PLAYING = "playing"
    """An ad is currently being rendered by VLC."""

    REPORTING = "reporting"
    """Flushing queued impressions to the server."""
