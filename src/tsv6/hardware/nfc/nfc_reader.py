#!/usr/bin/env python3
"""
NFC Card Reader using PN532 via USB/UART (CH340)

Reads NFC card UIDs using the PN532 NFC module. This can be used for
card-based authentication or identification.

Hardware: PN532 NFC module via USB/UART (CH340 adapter)
Protocol: PN532 HSU (High Speed UART) mode
"""

import os
import time
import logging
from typing import Optional, Tuple, Callable

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

logger = logging.getLogger(__name__)


# Configuration defaults
# PN532 is typically on ttyUSB5 (CH340), can be overridden via NFC_SERIAL_PORT env var
DEFAULT_SERIAL_PORT = os.getenv("NFC_SERIAL_PORT", "/dev/ttyUSB5")
DEFAULT_BAUD_RATE = 115200

# PN532 protocol constants
PN532_PREAMBLE = 0x00
PN532_STARTCODE1 = 0x00
PN532_STARTCODE2 = 0xFF
PN532_HOSTTOPN532 = 0xD4
PN532_PN532TOHOST = 0xD5

# Commands
CMD_GETFIRMWAREVERSION = 0x02
CMD_SAMCONFIGURATION = 0x14
CMD_INLISTPASSIVETARGET = 0x4A

# Card types
MIFARE_ISO14443A = 0x00


class PN532:
    """
    PN532 NFC Reader driver for HSU (High Speed UART) mode.

    Communicates with the PN532 via serial port to read NFC card UIDs.
    Supports Mifare Classic, Mifare Ultralight, and other ISO14443A cards.
    """

    def __init__(
        self,
        port: str = DEFAULT_SERIAL_PORT,
        baudrate: int = DEFAULT_BAUD_RATE
    ):
        """
        Initialize the PN532 reader.

        Args:
            port: Serial port path (e.g., '/dev/ttyUSB0')
            baudrate: Baud rate (typically 115200)

        Raises:
            ImportError: If pyserial is not installed
            serial.SerialException: If port cannot be opened
        """
        if not SERIAL_AVAILABLE:
            raise ImportError("pyserial is required for PN532. Install with: pip install pyserial")

        self.port = port
        self.baudrate = baudrate
        self.uart: Optional[serial.Serial] = None
        self._connected = False

        # Callback for card detection
        self.on_card_detected: Optional[Callable[[bytes], None]] = None

    def connect(self) -> bool:
        """
        Connect to the PN532 and initialize it.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.uart = serial.Serial(self.port, baudrate=self.baudrate, timeout=1)
            self._wake_up()

            # Verify connection by getting firmware version
            fw = self.get_firmware_version()
            if fw:
                ic, ver, rev, support = fw
                logger.info(f"PN532 connected - Firmware: {ver}.{rev}")
                self._connected = True

                # Configure SAM for normal mode
                self.sam_configuration()
                return True
            else:
                logger.error("Failed to communicate with PN532")
                self.disconnect()
                return False

        except serial.SerialException as e:
            logger.error(f"Serial error: {e}")
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    def disconnect(self):
        """Disconnect from the PN532."""
        if self.uart and self.uart.is_open:
            try:
                self.uart.close()
            except Exception:
                pass
        self.uart = None
        self._connected = False
        logger.info("PN532 disconnected")

    def is_connected(self) -> bool:
        """Check if connected to PN532."""
        return self._connected and self.uart is not None and self.uart.is_open

    def _wake_up(self):
        """Send wake-up sequence for HSU mode."""
        if self.uart:
            self.uart.write(b'\x55' * 16)
            time.sleep(0.1)
            self.uart.flushInput()

    def _build_frame(self, data: list) -> bytes:
        """
        Build a PN532 command frame.

        Frame format:
        - Preamble: 0x00
        - Start codes: 0x00, 0xFF
        - Length: LEN
        - Length checksum: LCS (LEN + LCS = 0x00)
        - Data: TFI + PD0...PDn
        - Data checksum: DCS (sum of all data + DCS = 0x00)
        - Postamble: 0x00

        Args:
            data: Command data bytes (including TFI)

        Returns:
            Complete frame as bytes
        """
        length = len(data)
        lcs = (~length + 1) & 0xFF
        dcs = (~sum(data) + 1) & 0xFF

        return bytes([
            PN532_PREAMBLE,
            PN532_STARTCODE1,
            PN532_STARTCODE2,
            length,
            lcs
        ] + list(data) + [dcs, 0x00])

    def _send_command(self, cmd: int, params: Optional[list] = None) -> Optional[bytes]:
        """
        Send a command and return the response data.

        Args:
            cmd: Command byte
            params: Optional command parameters

        Returns:
            Response data bytes, or None on error
        """
        if not self.uart:
            return None

        if params is None:
            params = []

        data = [PN532_HOSTTOPN532, cmd] + list(params)
        frame = self._build_frame(data)

        self.uart.write(frame)
        time.sleep(0.1)

        return self._read_response()

    def _read_response(self) -> Optional[bytes]:
        """
        Read and parse a response frame.

        Returns:
            Response data bytes (after TFI), or None on error
        """
        if not self.uart:
            return None

        # Read until we get enough data or timeout
        response = self.uart.read(100)
        if len(response) < 7:
            return None

        # Find the response frame (skip ACK if present)
        # Look for: 00 00 FF [length] [lcs] D5 ...
        idx = 0
        while idx < len(response) - 6:
            if (response[idx:idx+3] == b'\x00\x00\xff' and
                    response[idx+5] == PN532_PN532TOHOST):
                length = response[idx + 3]
                if idx + 6 + length <= len(response):
                    # Return data after TFI (D5)
                    return response[idx + 6:idx + 5 + length]
            idx += 1

        return None

    def get_firmware_version(self) -> Optional[Tuple[int, int, int, int]]:
        """
        Get PN532 firmware version.

        Returns:
            Tuple of (IC, Version, Revision, Support), or None on error
        """
        response = self._send_command(CMD_GETFIRMWAREVERSION)
        if response and len(response) >= 5:
            # Response: [cmd_response, IC, Ver, Rev, Support]
            return (response[1], response[2], response[3], response[4])
        return None

    def sam_configuration(self):
        """
        Configure the Security Access Module for normal mode.

        This must be called before reading cards.
        """
        # Mode=1 (normal), timeout=20 (1 second), IRQ=0
        self._send_command(CMD_SAMCONFIGURATION, [0x01, 0x14, 0x00])

    def read_passive_target(
        self,
        card_type: int = MIFARE_ISO14443A,
        timeout: float = 1.0
    ) -> Optional[bytes]:
        """
        Read a passive target (NFC card) and return its UID.

        Args:
            card_type: Card type to detect (default: ISO14443A)
            timeout: Read timeout in seconds

        Returns:
            Card UID as bytes, or None if no card detected
        """
        if not self.uart:
            return None

        # Set timeout for this read
        old_timeout = self.uart.timeout
        self.uart.timeout = timeout

        try:
            # MaxTg=1 (detect 1 card), BrTy=card_type
            response = self._send_command(CMD_INLISTPASSIVETARGET, [0x01, card_type])

            if response and len(response) > 5:
                num_targets = response[0]
                if num_targets > 0:
                    # Response: [NbTg, Tg, SENS_RES(2), SEL_RES, NFCIDLength, NFCID1...]
                    uid_length = response[4]
                    uid = response[5:5 + uid_length]

                    # Trigger callback if set
                    if self.on_card_detected:
                        self.on_card_detected(bytes(uid))

                    return bytes(uid)
        finally:
            self.uart.timeout = old_timeout

        return None

    def format_uid(self, uid: bytes) -> str:
        """
        Format UID bytes as a colon-separated hex string.

        Args:
            uid: UID bytes

        Returns:
            Formatted string like "04:A1:B2:C3:D4:E5:F6"
        """
        return ':'.join(f'{b:02X}' for b in uid)

    def read_continuous(self, callback: Optional[Callable[[bytes], None]] = None):
        """
        Continuously read NFC cards.

        Args:
            callback: Optional callback function called with UID when card detected.
                     If not provided, uses self.on_card_detected.

        Note: This method blocks forever until interrupted.
        """
        if callback:
            self.on_card_detected = callback

        logger.info("Starting continuous NFC card reading...")
        logger.info("(Press Ctrl+C to exit)")

        try:
            while True:
                uid = self.read_passive_target()
                if uid:
                    logger.info(f"Card detected: {self.format_uid(uid)}")
                    time.sleep(1)  # Debounce
        except KeyboardInterrupt:
            logger.info("Stopping card reader...")
        finally:
            self.disconnect()


if __name__ == '__main__':
    # Example usage
    logging.basicConfig(level=logging.INFO)

    print(f"Connecting to PN532 on {DEFAULT_SERIAL_PORT}...")

    reader = PN532()
    if reader.connect():
        print("Waiting for NFC card...")
        print("(Press Ctrl+C to exit)")
        print()

        def on_card(uid: bytes):
            print(f"Card UID: {reader.format_uid(uid)} ({len(uid)} bytes)")

        reader.read_continuous(callback=on_card)
    else:
        print("Failed to connect to PN532")
