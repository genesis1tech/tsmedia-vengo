#!/usr/bin/env python3
"""
AT Command Library for SIM7600NA-H 4G LTE HAT

Provides AT command definitions and response parsing for the SIM7600 modem.
Optimized for Hologram.io as the service provider.

Reference: https://www.waveshare.com/wiki/SIM7600NA-H_4G_HAT
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, List
from enum import Enum

logger = logging.getLogger(__name__)


class NetworkRegistrationStatus(Enum):
    """Network registration status codes from AT+CREG/AT+CGREG"""
    NOT_REGISTERED = 0
    REGISTERED_HOME = 1
    SEARCHING = 2
    DENIED = 3
    UNKNOWN = 4
    REGISTERED_ROAMING = 5


class NetworkMode(Enum):
    """Network mode settings for AT+CNMP"""
    AUTOMATIC = 2
    GSM_ONLY = 13
    WCDMA_ONLY = 14
    LTE_ONLY = 38
    GSM_WCDMA = 48
    GSM_LTE = 51
    WCDMA_LTE = 54
    GSM_WCDMA_LTE = 55


class FunctionalityMode(Enum):
    """Module functionality modes for AT+CFUN"""
    MINIMUM = 0  # Minimum functionality, RF disabled
    FULL = 1     # Full functionality
    DISABLE_TX = 2  # Disable transmit RF
    DISABLE_RX = 3  # Disable receive RF
    DISABLE_RF = 4  # Disable both TX and RX (airplane mode)
    FACTORY_TEST = 5
    RESET = 6    # Reset the module
    OFFLINE = 7  # Offline mode


@dataclass
class ATCommand:
    """
    AT command definition with expected response and timing parameters.

    Attributes:
        command: The AT command string (without 'AT' prefix for some commands)
        expected_response: Expected successful response (default "OK")
        timeout: Command timeout in seconds
        retries: Number of retry attempts on failure
        delay_after: Delay in seconds after command execution
    """
    command: str
    expected_response: str = "OK"
    timeout: float = 5.0
    retries: int = 3
    delay_after: float = 0.1

    def full_command(self) -> str:
        """Return the complete AT command string"""
        if self.command.upper().startswith("AT"):
            return self.command
        return f"AT{self.command}"


# Pre-defined AT commands for common operations
class ATCommands:
    """Collection of pre-defined AT commands for SIM7600"""

    # Basic commands
    AT = ATCommand("AT", timeout=2.0)
    ECHO_OFF = ATCommand("ATE0", timeout=2.0)
    ECHO_ON = ATCommand("ATE1", timeout=2.0)
    RESET = ATCommand("AT+CRESET", timeout=30.0, delay_after=5.0)

    # Module information
    MANUFACTURER = ATCommand("AT+CGMI", timeout=2.0)
    MODEL = ATCommand("AT+CGMM", timeout=2.0)
    FIRMWARE = ATCommand("AT+CGMR", timeout=2.0)
    IMEI = ATCommand("AT+CGSN", timeout=2.0)
    IMSI = ATCommand("AT+CIMI", timeout=2.0)

    # SIM card
    SIM_STATUS = ATCommand("AT+CPIN?", timeout=5.0)

    # Signal quality
    SIGNAL_QUALITY = ATCommand("AT+CSQ", timeout=5.0)
    SIGNAL_QUALITY_EXT = ATCommand("AT+CESQ", timeout=5.0)

    # Network registration
    NETWORK_REG = ATCommand("AT+CREG?", timeout=5.0)
    GPRS_REG = ATCommand("AT+CGREG?", timeout=5.0)
    EPS_REG = ATCommand("AT+CEREG?", timeout=5.0)
    OPERATOR = ATCommand("AT+COPS?", timeout=10.0)
    SYSTEM_INFO = ATCommand("AT+CPSI?", timeout=5.0)

    # Network mode
    SET_LTE_ONLY = ATCommand("AT+CNMP=38", timeout=5.0, delay_after=1.0)
    SET_AUTO_MODE = ATCommand("AT+CNMP=2", timeout=5.0, delay_after=1.0)
    GET_NETWORK_MODE = ATCommand("AT+CNMP?", timeout=5.0)

    # Functionality mode
    FULL_FUNCTIONALITY = ATCommand("AT+CFUN=1", timeout=10.0, delay_after=2.0)
    MINIMUM_FUNCTIONALITY = ATCommand("AT+CFUN=0", timeout=10.0, delay_after=2.0)
    AIRPLANE_MODE = ATCommand("AT+CFUN=4", timeout=10.0, delay_after=2.0)

    # APN/PDP context - Hologram.io defaults
    @staticmethod
    def set_apn(apn: str = "hologram", cid: int = 1) -> ATCommand:
        """Set APN for PDP context"""
        return ATCommand(f'AT+CGDCONT={cid},"IP","{apn}"', timeout=5.0)

    @staticmethod
    def get_apn() -> ATCommand:
        """Query PDP context settings"""
        return ATCommand("AT+CGDCONT?", timeout=5.0)

    # PDP context activation
    ATTACH_GPRS = ATCommand("AT+CGATT=1", timeout=30.0, delay_after=2.0)
    DETACH_GPRS = ATCommand("AT+CGATT=0", timeout=10.0, delay_after=1.0)
    GET_ATTACH_STATUS = ATCommand("AT+CGATT?", timeout=5.0)

    @staticmethod
    def activate_pdp(cid: int = 1) -> ATCommand:
        """Activate PDP context"""
        return ATCommand(f"AT+CGACT=1,{cid}", timeout=30.0, delay_after=2.0)

    @staticmethod
    def deactivate_pdp(cid: int = 1) -> ATCommand:
        """Deactivate PDP context"""
        return ATCommand(f"AT+CGACT=0,{cid}", timeout=10.0, delay_after=1.0)

    GET_PDP_STATUS = ATCommand("AT+CGACT?", timeout=5.0)

    # USB mode switching (RNDIS/NDIS/ECM)
    ENABLE_RNDIS = ATCommand("AT+CUSBPIDSWITCH=9011,1,1", timeout=30.0, delay_after=5.0)
    ENABLE_NDIS = ATCommand("AT+CUSBPIDSWITCH=9001,1,1", timeout=30.0, delay_after=5.0)
    GET_USB_MODE = ATCommand("AT+CUSBPIDSWITCH?", timeout=5.0)

    # NDIS dial-up (for data connection)
    NDIS_CONNECT = ATCommand("AT$QCRMCALL=1,1", timeout=30.0, delay_after=3.0)
    NDIS_DISCONNECT = ATCommand("AT$QCRMCALL=0,1", timeout=10.0, delay_after=1.0)

    # IP address
    GET_IP_ADDRESS = ATCommand("AT+CGPADDR", timeout=5.0)

    # Network open/close (for socket operations)
    NET_OPEN = ATCommand("AT+NETOPEN", timeout=30.0, delay_after=2.0)
    NET_CLOSE = ATCommand("AT+NETCLOSE", timeout=10.0, delay_after=1.0)


class ATResponseParser:
    """
    Parse AT command responses from SIM7600 modem.

    All parse methods return structured data from raw AT response strings.
    """

    @staticmethod
    def parse_csq(response: str) -> Tuple[int, int]:
        """
        Parse signal quality response (+CSQ: rssi,ber).

        Args:
            response: Raw AT response string

        Returns:
            Tuple of (rssi, ber) where:
            - rssi: 0-31 (signal strength) or 99 (unknown)
              - 0: -113 dBm or less
              - 1: -111 dBm
              - 2-30: -109 to -53 dBm
              - 31: -51 dBm or greater
              - 99: not known or not detectable
            - ber: 0-7 (bit error rate) or 99 (unknown)
        """
        match = re.search(r'\+CSQ:\s*(\d+),\s*(\d+)', response)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 99, 99  # Unknown

    @staticmethod
    def rssi_to_dbm(rssi: int) -> int:
        """
        Convert CSQ RSSI value to dBm.

        Args:
            rssi: CSQ RSSI value (0-31 or 99)

        Returns:
            Signal strength in dBm
        """
        if rssi == 0:
            return -113
        elif rssi == 1:
            return -111
        elif rssi == 99:
            return -999  # Unknown
        elif rssi >= 31:
            return -51
        else:
            return -113 + (rssi * 2)

    @staticmethod
    def parse_cops(response: str) -> Tuple[int, int, str, int]:
        """
        Parse operator information (+COPS: mode,format,oper,AcT).

        Args:
            response: Raw AT response string

        Returns:
            Tuple of (mode, format, operator_name, access_technology) where:
            - mode: 0=auto, 1=manual, 2=deregister, 3=set format only, 4=manual/auto
            - format: 0=long alphanumeric, 1=short alphanumeric, 2=numeric
            - operator_name: Operator name string
            - access_technology: 0=GSM, 2=UTRAN, 7=E-UTRAN (LTE)
        """
        # Try full format with operator
        match = re.search(r'\+COPS:\s*(\d+),(\d+),"([^"]*)",(\d+)', response)
        if match:
            return (
                int(match.group(1)),
                int(match.group(2)),
                match.group(3),
                int(match.group(4))
            )

        # Try minimal format (no operator registered)
        match = re.search(r'\+COPS:\s*(\d+)', response)
        if match:
            return int(match.group(1)), 0, "", 0

        return 0, 0, "", 0

    @staticmethod
    def parse_creg(response: str) -> Tuple[int, int]:
        """
        Parse network registration (+CREG: n,stat) or (+CGREG: n,stat).

        Args:
            response: Raw AT response string

        Returns:
            Tuple of (n, stat) where:
            - n: URC enable setting (0=disable, 1=enable, 2=enable with location)
            - stat: Registration status (see NetworkRegistrationStatus enum)
        """
        match = re.search(r'\+C[GE]?REG:\s*(\d+),\s*(\d+)', response)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 0, 0

    @staticmethod
    def parse_cgdcont(response: str) -> List[Dict[str, Any]]:
        """
        Parse PDP context definitions (+CGDCONT: cid,pdp_type,apn,...).

        Args:
            response: Raw AT response string

        Returns:
            List of PDP context dictionaries with keys: cid, pdp_type, apn
        """
        contexts = []
        for match in re.finditer(
            r'\+CGDCONT:\s*(\d+),"([^"]*)","([^"]*)"', response
        ):
            contexts.append({
                'cid': int(match.group(1)),
                'pdp_type': match.group(2),
                'apn': match.group(3)
            })
        return contexts

    @staticmethod
    def parse_cgatt(response: str) -> bool:
        """
        Parse GPRS attach status (+CGATT: state).

        Args:
            response: Raw AT response string

        Returns:
            True if attached, False otherwise
        """
        match = re.search(r'\+CGATT:\s*(\d+)', response)
        if match:
            return int(match.group(1)) == 1
        return False

    @staticmethod
    def parse_cgact(response: str) -> Dict[int, bool]:
        """
        Parse PDP context activation states (+CGACT: cid,state).

        Args:
            response: Raw AT response string

        Returns:
            Dictionary mapping CID to activation state
        """
        states = {}
        for match in re.finditer(r'\+CGACT:\s*(\d+),\s*(\d+)', response):
            states[int(match.group(1))] = int(match.group(2)) == 1
        return states

    @staticmethod
    def parse_cpin(response: str) -> str:
        """
        Parse SIM card status (+CPIN: status).

        Args:
            response: Raw AT response string

        Returns:
            SIM status string: "READY", "SIM PIN", "SIM PUK", etc.
        """
        match = re.search(r'\+CPIN:\s*(.+?)(?:\r|\n|$)', response)
        if match:
            return match.group(1).strip()
        return "UNKNOWN"

    @staticmethod
    def parse_cgpaddr(response: str) -> Dict[int, str]:
        """
        Parse IP addresses (+CGPADDR: cid,addr).

        Args:
            response: Raw AT response string

        Returns:
            Dictionary mapping CID to IP address
        """
        addresses = {}
        for match in re.finditer(r'\+CGPADDR:\s*(\d+),"?([^"\r\n]+)"?', response):
            addresses[int(match.group(1))] = match.group(2).strip()
        return addresses

    @staticmethod
    def parse_cpsi(response: str) -> Dict[str, Any]:
        """
        Parse system information (+CPSI: system_mode,operation_mode,...).

        Args:
            response: Raw AT response string

        Returns:
            Dictionary with system information
        """
        # LTE format: +CPSI: LTE,Online,460-00,0x2A4D,26828042,450,EUTRAN-BAND3,1650,5,5,-94,-860,-550,16
        match = re.search(r'\+CPSI:\s*([^,]+),([^,]+),([^,]*)', response)
        if match:
            return {
                'system_mode': match.group(1).strip(),
                'operation_mode': match.group(2).strip(),
                'operator_id': match.group(3).strip()
            }
        return {'system_mode': 'UNKNOWN', 'operation_mode': 'UNKNOWN', 'operator_id': ''}

    @staticmethod
    def parse_cusbpidswitch(response: str) -> Tuple[int, int]:
        """
        Parse USB mode (+CUSBPIDSWITCH: pid,mode).

        Args:
            response: Raw AT response string

        Returns:
            Tuple of (pid, mode) where pid indicates USB device mode
            - 9001: NDIS mode
            - 9011: RNDIS mode
            - 9018: ECM mode
        """
        match = re.search(r'\+CUSBPIDSWITCH:\s*(\d+),\s*(\d+)', response)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 0, 0

    @staticmethod
    def is_ok(response: str) -> bool:
        """Check if response contains OK"""
        return "OK" in response.upper()

    @staticmethod
    def is_error(response: str) -> bool:
        """Check if response contains ERROR"""
        return "ERROR" in response.upper()

    @staticmethod
    def get_error_code(response: str) -> Optional[int]:
        """
        Extract CME/CMS error code from response.

        Args:
            response: Raw AT response string

        Returns:
            Error code if present, None otherwise
        """
        match = re.search(r'\+CM[ES] ERROR:\s*(\d+)', response)
        if match:
            return int(match.group(1))
        return None


# Common CME error codes for reference
CME_ERRORS = {
    0: "Phone failure",
    1: "No connection to phone",
    3: "Operation not allowed",
    4: "Operation not supported",
    5: "PH-SIM PIN required",
    10: "SIM not inserted",
    11: "SIM PIN required",
    12: "SIM PUK required",
    13: "SIM failure",
    14: "SIM busy",
    15: "SIM wrong",
    16: "Incorrect password",
    17: "SIM PIN2 required",
    18: "SIM PUK2 required",
    20: "Memory full",
    21: "Invalid index",
    22: "Not found",
    23: "Memory failure",
    24: "Text string too long",
    25: "Invalid characters in text string",
    26: "Dial string too long",
    27: "Invalid characters in dial string",
    30: "No network service",
    31: "Network timeout",
    32: "Network not allowed - emergency calls only",
    100: "Unknown error",
}
