"""
NFC Hardware Module for TSV6

This module provides NFC tag emulation and card reading capabilities
using the PN532 NFC module via USB/UART.

Components:
- NFCEmulator: Emulates NFC Type 4 tags with dynamic URL (scanid as UTM)
- PN532: NFC card reader for reading card UIDs
- emulate_once: Convenience function for one-shot NFC emulation

Hardware Requirements:
- PN532 NFC module
- USB/UART adapter (CH340 or similar)
- libnfc-bin package (for nfc-emulate-forum-tag4)

Example Usage:
    from tsv6.hardware.nfc import NFCEmulator, PN532

    # Emulate NFC tag with scanid
    emulator = NFCEmulator()
    emulator.start_emulation(scanid="abc123")

    # Read NFC cards
    reader = PN532()
    if reader.connect():
        uid = reader.read_passive_target()
        print(f"Card UID: {reader.format_uid(uid)}")
"""

from .nfc_emulator import NFCEmulator, emulate_once
from .nfc_reader import PN532

__all__ = [
    'NFCEmulator',
    'PN532',
    'emulate_once',
]
