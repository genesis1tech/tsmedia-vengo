"""
SIM7600NA-H 4G LTE HAT Module

Controls Waveshare SIM7600NA-H 4G LTE HAT for cellular connectivity.
Optimized for Hologram.io as the service provider.

Usage:
    from tsv6.hardware.sim7600 import SIM7600Controller, SIM7600Config

    config = SIM7600Config(apn="hologram")
    controller = SIM7600Controller(config)

    if controller.connect():
        status = controller.get_network_status()
        print(f"Connected: {status['ip_address']}")

    controller.cleanup()

Reference:
    https://www.waveshare.com/wiki/SIM7600NA-H_4G_HAT
    https://support.hologram.io/hc/en-us/articles/360035697853-Set-the-device-APN
"""

from .controller import SIM7600Controller, SIM7600Config, ModemState
from .at_commands import (
    ATCommand,
    ATCommands,
    ATResponseParser,
    NetworkRegistrationStatus,
    NetworkMode,
    FunctionalityMode,
    CME_ERRORS,
)

__all__ = [
    # Controller
    'SIM7600Controller',
    'SIM7600Config',
    'ModemState',
    # AT Commands
    'ATCommand',
    'ATCommands',
    'ATResponseParser',
    'NetworkRegistrationStatus',
    'NetworkMode',
    'FunctionalityMode',
    'CME_ERRORS',
]
