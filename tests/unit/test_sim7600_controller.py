#!/usr/bin/env python3
"""
Unit tests for SIM7600 Controller

Tests the SIM7600NA-H 4G LTE HAT controller functionality.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import threading


class TestSIM7600Config:
    """Test SIM7600Config dataclass"""

    def test_default_config(self):
        """Test default configuration values for Hologram.io"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Config

        config = SIM7600Config()

        assert config.apn == "hologram"
        assert config.apn_username == ""
        assert config.apn_password == ""
        assert config.force_lte == True
        assert config.enable_roaming == True
        assert config.rndis_mode == True
        assert config.baudrate == 115200
        assert config.power_gpio == 6

    def test_custom_config(self):
        """Test custom configuration"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Config

        config = SIM7600Config(
            port="/dev/ttyUSB2",
            apn="custom.apn",
            force_lte=False,
            power_gpio=17
        )

        assert config.port == "/dev/ttyUSB2"
        assert config.apn == "custom.apn"
        assert config.force_lte == False
        assert config.power_gpio == 17


class TestATResponseParser:
    """Test AT response parsing"""

    def test_parse_csq_valid(self):
        """Test parsing valid signal quality response"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        response = "+CSQ: 20,0\r\n\r\nOK\r\n"
        rssi, ber = ATResponseParser.parse_csq(response)

        assert rssi == 20
        assert ber == 0

    def test_parse_csq_unknown(self):
        """Test parsing unknown signal quality"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        response = "+CSQ: 99,99\r\n\r\nOK\r\n"
        rssi, ber = ATResponseParser.parse_csq(response)

        assert rssi == 99
        assert ber == 99

    def test_parse_csq_invalid(self):
        """Test parsing invalid response returns unknown"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        response = "ERROR\r\n"
        rssi, ber = ATResponseParser.parse_csq(response)

        assert rssi == 99
        assert ber == 99

    def test_rssi_to_dbm_mapping(self):
        """Test RSSI to dBm conversion"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        assert ATResponseParser.rssi_to_dbm(0) == -113
        assert ATResponseParser.rssi_to_dbm(1) == -111
        assert ATResponseParser.rssi_to_dbm(10) == -93
        assert ATResponseParser.rssi_to_dbm(20) == -73
        assert ATResponseParser.rssi_to_dbm(31) == -51
        assert ATResponseParser.rssi_to_dbm(99) == -999

    def test_parse_cpin_ready(self):
        """Test parsing SIM ready status"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        response = "+CPIN: READY\r\n\r\nOK\r\n"
        status = ATResponseParser.parse_cpin(response)

        assert status == "READY"

    def test_parse_cpin_pin_required(self):
        """Test parsing SIM PIN required status"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        response = "+CPIN: SIM PIN\r\n\r\nOK\r\n"
        status = ATResponseParser.parse_cpin(response)

        assert status == "SIM PIN"

    def test_parse_cops_registered(self):
        """Test parsing operator info when registered"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        response = '+COPS: 0,0,"Hologram",7\r\n\r\nOK\r\n'
        mode, fmt, operator, act = ATResponseParser.parse_cops(response)

        assert mode == 0
        assert fmt == 0
        assert operator == "Hologram"
        assert act == 7  # LTE

    def test_parse_cgatt_attached(self):
        """Test parsing GPRS attach status"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        response = "+CGATT: 1\r\n\r\nOK\r\n"
        attached = ATResponseParser.parse_cgatt(response)

        assert attached == True

    def test_parse_cgatt_detached(self):
        """Test parsing GPRS detach status"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        response = "+CGATT: 0\r\n\r\nOK\r\n"
        attached = ATResponseParser.parse_cgatt(response)

        assert attached == False

    def test_is_ok(self):
        """Test OK detection"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        assert ATResponseParser.is_ok("OK\r\n") == True
        assert ATResponseParser.is_ok("+CSQ: 20,0\r\nOK\r\n") == True
        assert ATResponseParser.is_ok("ERROR\r\n") == False

    def test_is_error(self):
        """Test ERROR detection"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        assert ATResponseParser.is_error("ERROR\r\n") == True
        assert ATResponseParser.is_error("+CME ERROR: 10\r\n") == True
        assert ATResponseParser.is_error("OK\r\n") == False

    def test_get_error_code(self):
        """Test error code extraction"""
        from src.tsv6.hardware.sim7600.at_commands import ATResponseParser

        assert ATResponseParser.get_error_code("+CME ERROR: 10\r\n") == 10
        assert ATResponseParser.get_error_code("+CMS ERROR: 500\r\n") == 500
        assert ATResponseParser.get_error_code("ERROR\r\n") is None


class TestATCommands:
    """Test AT command definitions"""

    def test_at_command_full_command(self):
        """Test AT command string generation"""
        from src.tsv6.hardware.sim7600.at_commands import ATCommand

        cmd = ATCommand("AT+CSQ")
        assert cmd.full_command() == "AT+CSQ"

        cmd2 = ATCommand("+CPIN?")
        assert cmd2.full_command() == "AT+CPIN?"

    def test_set_apn_command(self):
        """Test APN configuration command"""
        from src.tsv6.hardware.sim7600.at_commands import ATCommands

        cmd = ATCommands.set_apn("hologram")
        assert cmd.command == 'AT+CGDCONT=1,"IP","hologram"'

    def test_pdp_activation_commands(self):
        """Test PDP context activation commands"""
        from src.tsv6.hardware.sim7600.at_commands import ATCommands

        activate = ATCommands.activate_pdp(1)
        assert activate.command == "AT+CGACT=1,1"

        deactivate = ATCommands.deactivate_pdp(1)
        assert deactivate.command == "AT+CGACT=0,1"


class TestSIM7600ControllerSimulation:
    """Test SIM7600 controller in simulation mode"""

    def test_simulation_connect(self):
        """Test connection in simulation mode"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config)

        result = controller.connect()

        assert result == True
        assert controller.is_connected() == True
        assert controller._state.value == "connected"

        controller.cleanup()

    def test_simulation_signal_quality(self):
        """Test signal quality in simulation mode"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config)
        controller.connect()

        rssi, ber = controller.get_signal_quality()

        assert rssi == 20  # Simulated value
        assert ber == 0

        controller.cleanup()

    def test_simulation_network_status(self):
        """Test network status in simulation mode"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config)
        controller.connect()

        status = controller.get_network_status()

        assert status['connected'] == True
        assert status['data_connected'] == True
        assert status['operator'] == "Hologram"
        assert 'ip_address' in status

        controller.cleanup()

    def test_simulation_disconnect(self):
        """Test disconnection in simulation mode"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config)
        controller.connect()

        result = controller.disconnect()

        assert result == True
        assert controller._connected == False

        controller.cleanup()

    def test_state_change_callback(self):
        """Test state change callback"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config, ModemState

        states = []

        def on_state_change(old_state, new_state):
            states.append((old_state, new_state))

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config, on_state_change=on_state_change)
        controller.connect()

        assert len(states) >= 2  # Should have initializing -> connected transitions

        controller.cleanup()


class TestSIM7600ControllerAutoDetect:
    """Test auto-detection functionality"""

    @patch('os.path.exists')
    def test_auto_detect_port(self, mock_exists):
        """Test auto-detection of serial port"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config

        # Simulate only the older fallback port exists
        def exists_side_effect(path):
            return path == '/dev/ttyUSB2'

        mock_exists.side_effect = exists_side_effect

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config)

        assert controller.port == '/dev/ttyUSB2'

        controller.cleanup()

    @patch('os.path.exists')
    def test_auto_detect_prefers_stable_sim7600_symlink(self, mock_exists):
        """Stable udev symlink beats ttyUSB numbering after USB reordering."""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config

        def exists_side_effect(path):
            return path in {'/dev/ttySIM7600', '/dev/ttyUSB2'}

        mock_exists.side_effect = exists_side_effect

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config)

        assert controller.port == '/dev/ttySIM7600'

        controller.cleanup()

    @patch('src.tsv6.hardware.sim7600.controller.glob.glob')
    @patch('os.path.exists')
    def test_auto_detect_prefers_simtech_interface_04_by_id(self, mock_exists, mock_glob):
        """Interface 04 by-id path wins when the /dev/ttySIM7600 rule is absent."""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config

        by_id = '/dev/serial/by-id/usb-SimTech__Incorporated-if04-port0'
        mock_glob.side_effect = lambda pattern: [by_id] if 'if04-port0' in pattern else []

        def exists_side_effect(path):
            return path in {by_id, '/dev/ttyUSB2'}

        mock_exists.side_effect = exists_side_effect

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config)

        assert controller.port == by_id

        controller.cleanup()

    @patch.dict('os.environ', {'TSV6_LTE_PORT': '/dev/custom-lte'})
    def test_env_var_override(self):
        """Test environment variable override"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config)

        assert controller.config.port == '/dev/custom-lte'

        controller.cleanup()

    @patch.dict('os.environ', {'TSV6_LTE_APN': 'custom.apn'})
    def test_env_var_apn_override(self):
        """Test APN environment variable override"""
        from src.tsv6.hardware.sim7600.controller import SIM7600Controller, SIM7600Config

        config = SIM7600Config(simulation_mode=True)
        controller = SIM7600Controller(config=config)

        assert controller.config.apn == 'custom.apn'

        controller.cleanup()


class TestModemState:
    """Test ModemState enum"""

    def test_modem_states(self):
        """Test all modem states are defined"""
        from src.tsv6.hardware.sim7600.controller import ModemState

        assert ModemState.UNKNOWN.value == "unknown"
        assert ModemState.POWERED_OFF.value == "powered_off"
        assert ModemState.INITIALIZING.value == "initializing"
        assert ModemState.SIM_ERROR.value == "sim_error"
        assert ModemState.SEARCHING.value == "searching"
        assert ModemState.REGISTERED.value == "registered"
        assert ModemState.CONNECTED.value == "connected"
        assert ModemState.ERROR.value == "error"
