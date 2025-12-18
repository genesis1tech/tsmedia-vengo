#!/usr/bin/env python3
"""
SIM7600NA-H 4G LTE HAT Controller for TSV6 Raspberry Pi

Controls Waveshare SIM7600NA-H 4G LTE HAT via USB serial interface.
Optimized for Hologram.io as the service provider.

Reference: https://www.waveshare.com/wiki/SIM7600NA-H_4G_HAT
"""

import os
import time
import threading
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, Callable
from enum import Enum

try:
    import serial
    PYSERIAL_AVAILABLE = True
except ImportError:
    PYSERIAL_AVAILABLE = False

from .at_commands import (
    ATCommand, ATCommands, ATResponseParser,
    NetworkRegistrationStatus, NetworkMode, FunctionalityMode,
    CME_ERRORS
)


logger = logging.getLogger(__name__)


class ModemState(Enum):
    """SIM7600 modem state"""
    UNKNOWN = "unknown"
    POWERED_OFF = "powered_off"
    INITIALIZING = "initializing"
    SIM_ERROR = "sim_error"
    SEARCHING = "searching"
    REGISTERED = "registered"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class SIM7600Config:
    """
    Configuration for SIM7600NA-H 4G LTE HAT.

    Defaults are optimized for Hologram.io service provider.
    """
    # Serial port settings
    port: Optional[str] = None  # Auto-detect if None
    baudrate: int = 115200

    # APN settings (Hologram.io defaults)
    apn: str = "hologram"
    apn_username: str = ""  # Hologram requires no authentication
    apn_password: str = ""

    # Network preferences
    force_lte: bool = True  # Use AT+CNMP=38 to force LTE mode
    enable_roaming: bool = True  # Required for Hologram global SIM
    rndis_mode: bool = True  # Use RNDIS USB network interface

    # GPIO for Raspberry Pi power control (GPIO D6 = BCM 6)
    power_gpio: int = 6
    use_gpio_power: bool = True

    # Timing settings
    command_timeout: float = 10.0
    connect_timeout: float = 60.0
    keepalive_interval: int = 30

    # Retry settings
    max_retries: int = 3
    retry_delay: float = 2.0

    # Simulation mode (for testing without hardware)
    simulation_mode: bool = False


class SIM7600Controller:
    """
    Controls SIM7600NA-H 4G LTE HAT via USB serial interface.

    Follows the same patterns as STServoController:
    - Auto-detection of serial ports
    - Environment variable overrides
    - Thread-safe operations
    - Simulation mode for testing

    Usage:
        controller = SIM7600Controller()
        if controller.connect():
            status = controller.get_network_status()
            print(f"Signal: {status['rssi']} ({status['rssi_dbm']} dBm)")
        controller.cleanup()
    """

    def __init__(
        self,
        config: Optional[SIM7600Config] = None,
        on_state_change: Optional[Callable[[ModemState, ModemState], None]] = None,
    ):
        """
        Initialize SIM7600 controller.

        Args:
            config: Configuration object (uses defaults if None)
            on_state_change: Callback function(old_state, new_state) on state changes
        """
        self.config = config or SIM7600Config()
        self.on_state_change = on_state_change

        # Load from environment variables (override config)
        self._load_from_env()

        # Serial connection
        self.port = self.config.port or self._auto_detect_port()
        self.serial: Optional[serial.Serial] = None

        # State tracking
        self._state = ModemState.UNKNOWN
        self._connected = False
        self._network_registered = False
        self._data_connected = False

        # Thread safety
        self.lock = threading.Lock()

        # Keepalive thread
        self._keepalive_thread: Optional[threading.Thread] = None
        self._stop_keepalive = threading.Event()

        # Cached network info
        self._last_signal_quality: Tuple[int, int] = (99, 99)
        self._last_operator: str = ""
        self._ip_address: str = ""

        # Check dependencies
        if not PYSERIAL_AVAILABLE:
            logger.warning("pyserial not available - running in simulation mode")
            self.config.simulation_mode = True

        logger.info(f"SIM7600Controller initialized (port={self.port}, apn={self.config.apn})")

    def _load_from_env(self) -> None:
        """Load configuration from environment variables."""
        env_port = os.environ.get('TSV6_LTE_PORT')
        if env_port:
            self.config.port = env_port

        env_baud = os.environ.get('TSV6_LTE_BAUD')
        if env_baud:
            self.config.baudrate = int(env_baud)

        env_apn = os.environ.get('TSV6_LTE_APN')
        if env_apn:
            self.config.apn = env_apn

        env_force_lte = os.environ.get('TSV6_LTE_FORCE_LTE')
        if env_force_lte:
            self.config.force_lte = env_force_lte.lower() in ('true', '1', 'yes')

        env_roaming = os.environ.get('TSV6_LTE_ROAMING')
        if env_roaming:
            self.config.enable_roaming = env_roaming.lower() in ('true', '1', 'yes')

        env_gpio = os.environ.get('TSV6_LTE_POWER_GPIO')
        if env_gpio:
            self.config.power_gpio = int(env_gpio)

        env_simulation = os.environ.get('TSV6_LTE_SIMULATION')
        if env_simulation:
            self.config.simulation_mode = env_simulation.lower() in ('true', '1', 'yes')

    def _auto_detect_port(self) -> str:
        """Auto-detect the serial port for the SIM7600 modem."""
        # SIM7600 creates multiple USB serial ports:
        # - ttyUSB0: Diagnostic
        # - ttyUSB1: GPS NMEA output
        # - ttyUSB2: AT commands
        # - ttyUSB3: Modem (PPP)
        ports = [
            '/dev/ttyUSB2',       # Primary AT command port
            '/dev/ttyUSB0',       # Fallback
            '/dev/ttyAMA0',       # Hardware UART on Pi
            '/dev/ttyS0',         # Alternate UART
            '/dev/tsv6-lte',      # Custom udev symlink if configured
            '/dev/serial/by-id/usb-SimTech__Incorporated_SimTech__Incorporated-if02-port0',
        ]

        for port in ports:
            if os.path.exists(port):
                logger.info(f"Auto-detected LTE modem port: {port}")
                return port

        logger.warning("No LTE modem port auto-detected, using /dev/ttyUSB2")
        return '/dev/ttyUSB2'

    def _set_state(self, new_state: ModemState) -> None:
        """Update modem state and trigger callback."""
        if new_state != self._state:
            old_state = self._state
            self._state = new_state
            logger.info(f"Modem state: {old_state.value} -> {new_state.value}")
            if self.on_state_change:
                try:
                    self.on_state_change(old_state, new_state)
                except Exception as e:
                    logger.error(f"State change callback error: {e}")

    @property
    def state(self) -> ModemState:
        """Get current modem state."""
        return self._state

    def connect(self) -> bool:
        """
        Initialize modem and establish data connection.

        Returns:
            True if successfully connected, False otherwise
        """
        if self.config.simulation_mode:
            return self._simulation_connect()

        with self.lock:
            try:
                self._set_state(ModemState.INITIALIZING)

                # Open serial port
                if not self._open_serial():
                    self._set_state(ModemState.ERROR)
                    return False

                # Basic modem check
                if not self._check_modem():
                    self._set_state(ModemState.ERROR)
                    return False

                # Check SIM card
                if not self._check_sim():
                    self._set_state(ModemState.SIM_ERROR)
                    return False

                # Configure APN
                if not self._configure_apn():
                    self._set_state(ModemState.ERROR)
                    return False

                # Force LTE mode if configured
                if self.config.force_lte:
                    self._set_network_mode(NetworkMode.LTE_ONLY)

                # Enable RNDIS mode for USB network interface
                if self.config.rndis_mode:
                    self._enable_rndis()

                # Wait for network registration
                self._set_state(ModemState.SEARCHING)
                if not self._wait_for_registration():
                    logger.warning("Network registration timeout, but continuing...")

                # Establish data connection
                if not self._establish_data_connection():
                    logger.warning("Data connection failed, will retry...")

                self._connected = True
                self._set_state(ModemState.CONNECTED)

                # Start keepalive thread
                self._start_keepalive()

                logger.info("SIM7600 connected successfully")
                return True

            except Exception as e:
                logger.error(f"Connection failed: {e}")
                self._set_state(ModemState.ERROR)
                return False

    def _simulation_connect(self) -> bool:
        """Simulate connection for testing."""
        logger.info("[SIM] Simulating modem connection")
        self._set_state(ModemState.INITIALIZING)
        time.sleep(0.5)
        self._set_state(ModemState.SEARCHING)
        time.sleep(0.5)
        self._connected = True
        self._network_registered = True
        self._data_connected = True
        self._last_signal_quality = (20, 0)
        self._last_operator = "Hologram"
        self._ip_address = "10.170.1.100"
        self._set_state(ModemState.CONNECTED)
        return True

    def _open_serial(self) -> bool:
        """Open serial port connection."""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.config.baudrate,
                timeout=self.config.command_timeout,
                write_timeout=self.config.command_timeout,
            )
            logger.info(f"Serial port opened: {self.port}")
            time.sleep(0.5)  # Allow modem to stabilize
            return True
        except Exception as e:
            logger.error(f"Failed to open serial port {self.port}: {e}")
            return False

    def _send_command(
        self,
        cmd: ATCommand,
        check_ok: bool = True,
    ) -> Tuple[bool, str]:
        """
        Send AT command and get response.

        Args:
            cmd: ATCommand object
            check_ok: Check for OK in response

        Returns:
            Tuple of (success, response_string)
        """
        if self.config.simulation_mode:
            return True, "OK"

        if not self.serial or not self.serial.is_open:
            return False, "Serial port not open"

        full_cmd = cmd.full_command()

        for attempt in range(cmd.retries):
            try:
                # Clear input buffer
                self.serial.reset_input_buffer()

                # Send command
                logger.debug(f"TX: {full_cmd}")
                self.serial.write(f"{full_cmd}\r\n".encode())
                self.serial.flush()

                # Read response
                response = ""
                end_time = time.time() + cmd.timeout
                while time.time() < end_time:
                    if self.serial.in_waiting:
                        chunk = self.serial.read(self.serial.in_waiting).decode('utf-8', errors='ignore')
                        response += chunk

                        # Check for completion
                        if "OK" in response or "ERROR" in response:
                            break
                    else:
                        time.sleep(0.05)

                logger.debug(f"RX: {response.strip()}")

                # Check for errors
                if ATResponseParser.is_error(response):
                    error_code = ATResponseParser.get_error_code(response)
                    error_msg = CME_ERRORS.get(error_code, "Unknown error") if error_code else "Unknown error"
                    logger.warning(f"AT command error: {error_msg} (code: {error_code})")
                    if attempt < cmd.retries - 1:
                        time.sleep(cmd.delay_after)
                        continue
                    return False, response

                # Check for OK if required
                if check_ok and not ATResponseParser.is_ok(response):
                    if attempt < cmd.retries - 1:
                        time.sleep(cmd.delay_after)
                        continue
                    return False, response

                # Success
                if cmd.delay_after > 0:
                    time.sleep(cmd.delay_after)
                return True, response

            except Exception as e:
                logger.error(f"AT command exception: {e}")
                if attempt < cmd.retries - 1:
                    time.sleep(cmd.delay_after)
                    continue
                return False, str(e)

        return False, "Max retries exceeded"

    def _check_modem(self) -> bool:
        """Verify modem is responding."""
        success, response = self._send_command(ATCommands.AT)
        if not success:
            logger.error("Modem not responding")
            return False

        # Disable echo for cleaner responses
        self._send_command(ATCommands.ECHO_OFF, check_ok=False)

        logger.info("Modem responding")
        return True

    def _check_sim(self) -> bool:
        """Check SIM card status."""
        success, response = self._send_command(ATCommands.SIM_STATUS)
        if not success:
            logger.error("Failed to check SIM status")
            return False

        status = ATResponseParser.parse_cpin(response)
        logger.info(f"SIM status: {status}")

        if status == "READY":
            return True
        elif status == "SIM PIN":
            logger.error("SIM PIN required - not implemented")
            return False
        else:
            logger.error(f"SIM error: {status}")
            return False

    def _configure_apn(self) -> bool:
        """Configure APN for data connection."""
        apn_cmd = ATCommands.set_apn(self.config.apn)
        success, response = self._send_command(apn_cmd)

        if not success:
            logger.error(f"Failed to set APN: {self.config.apn}")
            return False

        logger.info(f"APN configured: {self.config.apn}")
        return True

    def _set_network_mode(self, mode: NetworkMode) -> bool:
        """Set network mode (LTE only, auto, etc.)."""
        if mode == NetworkMode.LTE_ONLY:
            cmd = ATCommands.SET_LTE_ONLY
        else:
            cmd = ATCommands.SET_AUTO_MODE

        success, _ = self._send_command(cmd)
        if success:
            logger.info(f"Network mode set: {mode.name}")
        return success

    def _enable_rndis(self) -> bool:
        """Enable RNDIS USB network interface mode."""
        success, response = self._send_command(ATCommands.GET_USB_MODE)
        if success:
            pid, mode = ATResponseParser.parse_cusbpidswitch(response)
            if pid == 9011:
                logger.info("RNDIS mode already enabled")
                return True

        success, _ = self._send_command(ATCommands.ENABLE_RNDIS)
        if success:
            logger.info("RNDIS mode enabled - modem will restart")
            # Modem restarts after USB mode change, need to wait
            time.sleep(10)
            # Reopen serial port
            if self.serial:
                self.serial.close()
            return self._open_serial()
        return False

    def _wait_for_registration(self, timeout: float = 60.0) -> bool:
        """Wait for network registration."""
        end_time = time.time() + timeout
        registered_statuses = [
            NetworkRegistrationStatus.REGISTERED_HOME.value,
            NetworkRegistrationStatus.REGISTERED_ROAMING.value,
        ]

        while time.time() < end_time:
            success, response = self._send_command(ATCommands.EPS_REG)
            if success:
                _, stat = ATResponseParser.parse_creg(response)
                if stat in registered_statuses:
                    self._network_registered = True
                    logger.info(f"Network registered (status: {stat})")

                    # Get operator info
                    success, response = self._send_command(ATCommands.OPERATOR)
                    if success:
                        _, _, operator, _ = ATResponseParser.parse_cops(response)
                        self._last_operator = operator
                        logger.info(f"Operator: {operator}")

                    return True

                logger.debug(f"Registration status: {stat}, waiting...")

            time.sleep(2)

        logger.warning("Network registration timeout")
        return False

    def _establish_data_connection(self) -> bool:
        """Establish data connection via NDIS."""
        # Attach to GPRS
        success, _ = self._send_command(ATCommands.ATTACH_GPRS)
        if not success:
            logger.warning("GPRS attach failed")

        # Activate PDP context
        activate_cmd = ATCommands.activate_pdp(1)
        success, _ = self._send_command(activate_cmd)
        if not success:
            logger.warning("PDP activation failed")

        # Start NDIS dial
        success, _ = self._send_command(ATCommands.NDIS_CONNECT)
        if not success:
            logger.warning("NDIS connection failed")
            return False

        # Get IP address
        time.sleep(2)
        success, response = self._send_command(ATCommands.GET_IP_ADDRESS)
        if success:
            addresses = ATResponseParser.parse_cgpaddr(response)
            if addresses:
                self._ip_address = addresses.get(1, "")
                logger.info(f"IP address: {self._ip_address}")

        self._data_connected = True
        return True

    def disconnect(self) -> bool:
        """
        Gracefully disconnect from network.

        Returns:
            True if successfully disconnected
        """
        logger.info("Disconnecting SIM7600...")
        self._stop_keepalive.set()

        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_thread.join(timeout=5.0)

        if self.config.simulation_mode:
            self._connected = False
            self._data_connected = False
            self._set_state(ModemState.POWERED_OFF)
            return True

        with self.lock:
            try:
                # Disconnect NDIS
                self._send_command(ATCommands.NDIS_DISCONNECT, check_ok=False)

                # Deactivate PDP
                deactivate_cmd = ATCommands.deactivate_pdp(1)
                self._send_command(deactivate_cmd, check_ok=False)

                # Detach from GPRS
                self._send_command(ATCommands.DETACH_GPRS, check_ok=False)

                self._data_connected = False
                self._network_registered = False
                self._connected = False
                self._set_state(ModemState.POWERED_OFF)

                logger.info("SIM7600 disconnected")
                return True

            except Exception as e:
                logger.error(f"Disconnect error: {e}")
                return False

    def get_signal_quality(self) -> Tuple[int, int]:
        """
        Get signal quality.

        Returns:
            Tuple of (rssi, ber) where:
            - rssi: 0-31 signal strength (99 = unknown)
            - ber: 0-7 bit error rate (99 = unknown)
        """
        if self.config.simulation_mode:
            return self._last_signal_quality

        with self.lock:
            success, response = self._send_command(ATCommands.SIGNAL_QUALITY)
            if success:
                self._last_signal_quality = ATResponseParser.parse_csq(response)
            return self._last_signal_quality

    def get_signal_dbm(self) -> int:
        """
        Get signal strength in dBm.

        Returns:
            Signal strength in dBm (e.g., -85)
        """
        rssi, _ = self.get_signal_quality()
        return ATResponseParser.rssi_to_dbm(rssi)

    def get_network_status(self) -> Dict[str, Any]:
        """
        Get comprehensive network status.

        Returns:
            Dictionary with network information
        """
        rssi, ber = self.get_signal_quality()

        status = {
            'state': self._state.value,
            'connected': self._connected,
            'network_registered': self._network_registered,
            'data_connected': self._data_connected,
            'rssi': rssi,
            'rssi_dbm': ATResponseParser.rssi_to_dbm(rssi),
            'ber': ber,
            'operator': self._last_operator,
            'ip_address': self._ip_address,
            'apn': self.config.apn,
            'port': self.port,
        }

        # Get additional info if connected
        if self._connected and not self.config.simulation_mode:
            with self.lock:
                # System info
                success, response = self._send_command(ATCommands.SYSTEM_INFO)
                if success:
                    sys_info = ATResponseParser.parse_cpsi(response)
                    status['system_mode'] = sys_info.get('system_mode', 'UNKNOWN')
                    status['operation_mode'] = sys_info.get('operation_mode', 'UNKNOWN')

        return status

    def is_connected(self) -> bool:
        """Check if modem is connected and has data connectivity."""
        return self._connected and self._data_connected

    def restart_modem(self) -> bool:
        """
        Soft restart the modem.

        Returns:
            True if restart initiated successfully
        """
        logger.info("Restarting modem...")

        if self.config.simulation_mode:
            self._set_state(ModemState.INITIALIZING)
            time.sleep(1)
            self._set_state(ModemState.CONNECTED)
            return True

        with self.lock:
            self._set_state(ModemState.INITIALIZING)

            # Soft reset via AT command
            success, _ = self._send_command(ATCommands.RESET)
            if success:
                time.sleep(10)  # Wait for modem to restart

                # Close and reopen serial
                if self.serial:
                    self.serial.close()

                return self.connect()

            return False

    def power_cycle(self) -> bool:
        """
        Hard reset modem via GPIO power control.

        Returns:
            True if power cycle completed
        """
        if not self.config.use_gpio_power:
            logger.warning("GPIO power control not enabled")
            return False

        logger.info(f"Power cycling modem via GPIO {self.config.power_gpio}...")
        self._set_state(ModemState.INITIALIZING)

        try:
            # Use pinctrl on Pi 5 or gpiod on Pi 4
            gpio = self.config.power_gpio

            # Power off
            subprocess.run(['pinctrl', 'set', str(gpio), 'op', 'dl'], timeout=5)
            time.sleep(3)

            # Power on
            subprocess.run(['pinctrl', 'set', str(gpio), 'op', 'dh'], timeout=5)
            time.sleep(10)  # Wait for modem to boot

            logger.info("Power cycle complete, reconnecting...")
            return self.connect()

        except Exception as e:
            logger.error(f"Power cycle failed: {e}")
            return False

    def _start_keepalive(self) -> None:
        """Start keepalive monitoring thread."""
        self._stop_keepalive.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            name="SIM7600-Keepalive",
            daemon=True
        )
        self._keepalive_thread.start()

    def _keepalive_loop(self) -> None:
        """Background thread for keepalive monitoring."""
        logger.info("Keepalive thread started")

        while not self._stop_keepalive.wait(self.config.keepalive_interval):
            try:
                # Check signal quality (also serves as keepalive)
                rssi, _ = self.get_signal_quality()

                if rssi == 99:
                    logger.warning("Signal lost (RSSI=99)")

            except Exception as e:
                logger.error(f"Keepalive error: {e}")

        logger.info("Keepalive thread stopped")

    def cleanup(self) -> None:
        """Release all resources."""
        logger.info("Cleaning up SIM7600 controller...")

        self._stop_keepalive.set()
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_thread.join(timeout=5.0)

        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
            except Exception:
                pass

        self._connected = False
        self._set_state(ModemState.POWERED_OFF)
        logger.info("SIM7600 controller cleanup complete")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.cleanup()
        return False
