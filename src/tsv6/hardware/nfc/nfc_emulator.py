#!/usr/bin/env python3
"""
NFC Tag Emulator - Emulates an NFC Type 4 tag with a URL

Uses libnfc's nfc-emulate-forum-tag4 for reliable iPhone/Android support.
The URL includes a unique scanid as the UTM parameter for tracking.

Hardware: PN532 NFC module via USB/UART (CH340)
"""

import subprocess
import tempfile
import os
import time
import threading
import logging
from typing import Optional, Callable

# Try to import serial for PN532 wake-up
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

logger = logging.getLogger(__name__)


# Default configuration
DEFAULT_BASE_URL = "genesis1.tech"
# PN532 is typically on ttyUSB5 (CH340), can be overridden via NFC_SERIAL_PORT env var
DEFAULT_SERIAL_PORT = os.getenv("NFC_SERIAL_PORT", "/dev/ttyUSB5")
DEFAULT_BAUD_RATE = 115200
DEFAULT_EMULATION_TIMEOUT = 120  # seconds


class NFCEmulator:
    """
    NFC Tag Emulator that broadcasts a URL with dynamic UTM parameter.

    Uses libnfc's nfc-emulate-forum-tag4 to emulate an NFC Forum Type 4 tag
    containing an NDEF URI record. When a phone taps, it opens the URL.

    The scanid (transaction ID) is embedded as the UTM parameter for tracking.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        serial_port: str = DEFAULT_SERIAL_PORT,
        baud_rate: int = DEFAULT_BAUD_RATE,
        timeout: int = DEFAULT_EMULATION_TIMEOUT
    ):
        """
        Initialize the NFC Emulator.

        Args:
            base_url: Base URL without protocol (e.g., "genesis1.tech")
            serial_port: Serial port for PN532 (e.g., "/dev/ttyUSB0")
            baud_rate: Baud rate for serial communication
            timeout: Emulation timeout in seconds
        """
        self.base_url = base_url
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.timeout = timeout

        self._emulation_thread: Optional[threading.Thread] = None
        self._emulation_process: Optional[subprocess.Popen] = None
        self._running = False
        self._current_scanid: Optional[str] = None

        # Callback for when tag is read by a phone
        self.on_tag_read: Optional[Callable[[str], None]] = None

        # Callback for emulation status updates
        self.on_status_change: Optional[Callable[[str, str], None]] = None

    def _build_ndef_uri(self, url: str) -> bytes:
        """
        Build NDEF URI record for https:// URL.

        NDEF URI record format:
        - Record header: 0xD1 (MB=1, ME=1, CF=0, SR=1, IL=0, TNF=1 (well-known))
        - Type length: 0x01
        - Payload length: variable
        - Type: 0x55 ('U' for URI)
        - Payload: 0x04 (https://) + URL without protocol

        Args:
            url: URL without https:// prefix

        Returns:
            NDEF message bytes
        """
        # 0x04 = https:// URI identifier code
        payload = bytes([0x04]) + url.encode('utf-8')

        # NDEF record: MB=1, ME=1, SR=1, TNF=1 (well-known)
        # Type: 'U' (0x55) for URI
        return bytes([0xD1, 0x01, len(payload), 0x55]) + payload

    def _wake_pn532(self) -> bool:
        """
        Wake up PN532 before use.

        The PN532 in HSU mode needs a wake-up sequence (0x55 bytes)
        before it will respond to commands.

        Returns:
            True if wake-up was successful, False otherwise
        """
        if not SERIAL_AVAILABLE:
            logger.warning("pyserial not available, skipping PN532 wake-up")
            return True  # Proceed anyway, libnfc may handle it

        try:
            if not os.path.exists(self.serial_port):
                logger.warning(f"Serial port {self.serial_port} not found")
                return False

            s = serial.Serial(self.serial_port, self.baud_rate, timeout=0.5)
            s.write(b'\x55' * 16)  # Wake-up sequence
            time.sleep(0.1)
            s.close()
            logger.debug("PN532 wake-up sequence sent")
            return True
        except serial.SerialException as e:
            logger.error(f"Failed to wake PN532: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during PN532 wake-up: {e}")
            return False

    def _build_url(self, scanid: str) -> str:
        """
        Build the full URL with scanid as UTM parameter.

        Args:
            scanid: Unique scan/transaction ID

        Returns:
            Full URL (without protocol, as NDEF handles that)
        """
        return f"{self.base_url}?utm={scanid}"

    def start_emulation(self, scanid: str) -> bool:
        """
        Start NFC tag emulation with the given scanid.

        This method starts emulation in a background thread. The tag will
        broadcast a URL with the scanid as the UTM parameter until:
        - A phone reads the tag
        - The timeout expires
        - stop_emulation() is called

        Args:
            scanid: Unique scan/transaction ID to embed in the URL

        Returns:
            True if emulation started successfully, False otherwise
        """
        if self._running:
            logger.warning("Emulation already running, stopping first")
            self.stop_emulation()

        self._current_scanid = scanid
        self._running = True

        self._emulation_thread = threading.Thread(
            target=self._emulation_worker,
            args=(scanid,),
            name="NFCEmulator",
            daemon=True
        )
        self._emulation_thread.start()

        logger.info(f"NFC emulation started with scanid: {scanid}")
        return True

    def _emulation_worker(self, scanid: str):
        """
        Background worker thread for NFC emulation.

        Args:
            scanid: Unique scan/transaction ID
        """
        ndef_file = None

        try:
            # Build URL and NDEF data
            url = self._build_url(scanid)
            ndef_data = self._build_ndef_uri(url)

            logger.info(f"Emulating NFC tag with URL: https://{url}")

            # Notify status change
            if self.on_status_change:
                self.on_status_change("started", scanid)

            # Write NDEF data to temp file
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.bin', delete=False) as f:
                f.write(ndef_data)
                ndef_file = f.name

            # Wake up PN532
            if not self._wake_pn532():
                logger.warning("PN532 wake-up failed, attempting emulation anyway")

            # Start nfc-emulate-forum-tag4
            self._emulation_process = subprocess.Popen(
                ['nfc-emulate-forum-tag4', ndef_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            try:
                stdout, stderr = self._emulation_process.communicate(timeout=self.timeout)

                # Check if tag was read
                if 'Target Released' in stderr or self._emulation_process.returncode == 0:
                    logger.info(f"NFC tag read! scanid: {scanid}")
                    if self.on_tag_read:
                        self.on_tag_read(scanid)
                    if self.on_status_change:
                        self.on_status_change("read", scanid)
                else:
                    logger.debug(f"Emulation ended without read. stderr: {stderr}")
                    if self.on_status_change:
                        self.on_status_change("timeout", scanid)

            except subprocess.TimeoutExpired:
                logger.info("NFC emulation timeout - no phone detected")
                self._emulation_process.kill()
                if self.on_status_change:
                    self.on_status_change("timeout", scanid)

        except FileNotFoundError:
            logger.error("nfc-emulate-forum-tag4 not found. Install libnfc-bin package.")
            if self.on_status_change:
                self.on_status_change("error", scanid)
        except Exception as e:
            logger.error(f"NFC emulation error: {e}")
            if self.on_status_change:
                self.on_status_change("error", scanid)
        finally:
            # Cleanup temp file
            if ndef_file and os.path.exists(ndef_file):
                try:
                    os.unlink(ndef_file)
                except Exception:
                    pass

            self._running = False
            self._emulation_process = None
            self._current_scanid = None

    def stop_emulation(self):
        """Stop any running NFC emulation."""
        if self._emulation_process:
            try:
                self._emulation_process.terminate()
                self._emulation_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._emulation_process.kill()
            except Exception as e:
                logger.error(f"Error stopping emulation: {e}")
            finally:
                self._emulation_process = None

        self._running = False
        logger.info("NFC emulation stopped")

    def is_running(self) -> bool:
        """Check if emulation is currently running."""
        return self._running

    def get_current_scanid(self) -> Optional[str]:
        """Get the scanid of the currently running emulation."""
        return self._current_scanid if self._running else None


def emulate_once(scanid: str, base_url: str = DEFAULT_BASE_URL, timeout: int = 120) -> bool:
    """
    Convenience function for one-shot NFC emulation.

    Blocks until the tag is read, timeout expires, or an error occurs.

    Args:
        scanid: Unique scan/transaction ID to embed in the URL
        base_url: Base URL (default: genesis1.tech)
        timeout: Emulation timeout in seconds

    Returns:
        True if tag was read, False otherwise
    """
    tag_read = threading.Event()

    emulator = NFCEmulator(base_url=base_url, timeout=timeout)
    emulator.on_tag_read = lambda sid: tag_read.set()

    if emulator.start_emulation(scanid):
        # Wait for tag read or timeout
        tag_read.wait(timeout=timeout + 5)  # Extra 5s buffer
        emulator.stop_emulation()
        return tag_read.is_set()

    return False


if __name__ == '__main__':
    # Example usage
    import uuid

    logging.basicConfig(level=logging.INFO)

    scanid = str(uuid.uuid4())
    print(f"Starting NFC emulation with scanid: {scanid}")
    print(f"URL: https://genesis1.tech?utm={scanid}")
    print()
    print("Tap your phone to open the URL!")
    print("(Press Ctrl+C to exit)")
    print()

    try:
        result = emulate_once(scanid)
        if result:
            print("Tag was read!")
        else:
            print("No phone detected.")
    except KeyboardInterrupt:
        print("\nExiting...")
