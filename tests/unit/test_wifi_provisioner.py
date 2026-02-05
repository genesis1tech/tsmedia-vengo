"""
Unit tests for WiFi Provisioner.

Tests the immediate broadcast behavior when saved network is not found.
"""
import pytest
import os
import tempfile
from unittest.mock import Mock, patch, MagicMock
from src.tsv6.provisioning.wifi_provisioner import (
    WiFiProvisioner,
    ProvisioningConfig,
    ProvisioningResult
)


class TestProvisioningConfig:
    """Test ProvisioningConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ProvisioningConfig()
        assert config.enabled is True
        assert config.timeout_seconds == 600
        assert config.connection_test_timeout == 30
        assert config.ap_interface == "wlan0"
        assert config.ap_ip == "192.168.4.1"
        assert config.ap_ssid_prefix == "TS_"
        assert config.ap_password == "recycleit"
        assert config.ap_channel == 7
        assert config.web_port == 80


class TestWiFiProvisioner:
    """Test WiFiProvisioner class."""

    @pytest.fixture
    def temp_wpa_config(self):
        """Create a temporary wpa_supplicant.conf file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            yield f.name
        # Cleanup
        if os.path.exists(f.name):
            os.unlink(f.name)

    @pytest.fixture
    def config(self, temp_wpa_config):
        """Create test configuration."""
        return ProvisioningConfig(
            enabled=True,
            timeout_seconds=10,
            connection_test_timeout=5,
            wpa_supplicant_conf=temp_wpa_config
        )

    @pytest.fixture
    def provisioner(self, config):
        """Create WiFiProvisioner instance for testing."""
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_initialization(self, config):
        """Test WiFiProvisioner initialization."""
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            provisioner = WiFiProvisioner(config=config)
            assert provisioner.config == config
            assert provisioner.device_id == '12345678'
            assert provisioner.ap_ssid == 'TS_12345678'

    def test_get_device_id_failure(self, config):
        """Test device ID retrieval failure."""
        with patch('builtins.open', side_effect=FileNotFoundError()):
            provisioner = WiFiProvisioner(config=config)
            assert provisioner.device_id == 'UNKNOWN'


class TestHasNetworkConfig:
    """Test _has_network_config method."""

    @pytest.fixture
    def temp_wpa_config(self):
        """Create a temporary wpa_supplicant.conf file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    @pytest.fixture
    def config(self, temp_wpa_config):
        """Create test configuration."""
        return ProvisioningConfig(wpa_supplicant_conf=temp_wpa_config)

    @pytest.fixture
    def provisioner(self, config):
        """Create WiFiProvisioner instance."""
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_has_network_config_with_network_block(self, provisioner, temp_wpa_config):
        """Test detection of network block in config."""
        with open(temp_wpa_config, 'w') as f:
            f.write("""
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

network={
    ssid="MyHomeWiFi"
    psk="secretpassword"
    key_mgmt=WPA-PSK
}
""")
        assert provisioner._has_network_config() is True

    def test_has_network_config_empty_file(self, provisioner, temp_wpa_config):
        """Test detection with empty config file."""
        with open(temp_wpa_config, 'w') as f:
            f.write("""
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US
""")
        assert provisioner._has_network_config() is False

    def test_has_network_config_no_file(self, provisioner):
        """Test detection when config file doesn't exist."""
        provisioner.config.wpa_supplicant_conf = '/nonexistent/path.conf'
        assert provisioner._has_network_config() is False


class TestGetSavedSSIDs:
    """Test _get_saved_ssids method."""

    @pytest.fixture
    def temp_wpa_config(self):
        """Create a temporary wpa_supplicant.conf file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    @pytest.fixture
    def config(self, temp_wpa_config):
        """Create test configuration."""
        return ProvisioningConfig(wpa_supplicant_conf=temp_wpa_config)

    @pytest.fixture
    def provisioner(self, config):
        """Create WiFiProvisioner instance."""
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_get_saved_ssids_single_network(self, provisioner, temp_wpa_config):
        """Test extraction of single saved SSID."""
        with open(temp_wpa_config, 'w') as f:
            f.write("""
network={
    ssid="MyHomeWiFi"
    psk="secretpassword"
}
""")
        ssids = provisioner._get_saved_ssids()
        assert ssids == ["MyHomeWiFi"]

    def test_get_saved_ssids_multiple_networks(self, provisioner, temp_wpa_config):
        """Test extraction of multiple saved SSIDs."""
        with open(temp_wpa_config, 'w') as f:
            f.write("""
network={
    ssid="HomeWiFi"
    psk="password1"
}
network={
    ssid="OfficeWiFi"
    psk="password2"
}
network={
    ssid="CoffeeShop"
    psk="password3"
}
""")
        ssids = provisioner._get_saved_ssids()
        assert ssids == ["HomeWiFi", "OfficeWiFi", "CoffeeShop"]

    def test_get_saved_ssids_empty_file(self, provisioner, temp_wpa_config):
        """Test extraction from empty config."""
        with open(temp_wpa_config, 'w') as f:
            f.write("# No networks configured")
        ssids = provisioner._get_saved_ssids()
        assert ssids == []

    def test_get_saved_ssids_no_file(self, provisioner):
        """Test extraction when file doesn't exist."""
        provisioner.config.wpa_supplicant_conf = '/nonexistent/path.conf'
        ssids = provisioner._get_saved_ssids()
        assert ssids == []


class TestIsSavedNetworkVisible:
    """Test _is_saved_network_visible method."""

    @pytest.fixture
    def temp_wpa_config(self):
        """Create a temporary wpa_supplicant.conf file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    @pytest.fixture
    def config(self, temp_wpa_config):
        """Create test configuration."""
        return ProvisioningConfig(wpa_supplicant_conf=temp_wpa_config)

    @pytest.fixture
    def provisioner(self, config):
        """Create WiFiProvisioner instance."""
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_saved_network_visible(self, provisioner, temp_wpa_config):
        """Test when saved network IS visible in scan."""
        with open(temp_wpa_config, 'w') as f:
            f.write('network={\n    ssid="MyHomeWiFi"\n    psk="secret"\n}')

        # Mock scan to return network list including saved network
        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan:
            mock_scan.return_value = [
                {'ssid': 'Neighbor1', 'signal': -60},
                {'ssid': 'MyHomeWiFi', 'signal': -45},
                {'ssid': 'Neighbor2', 'signal': -70}
            ]
            assert provisioner._is_saved_network_visible() is True

    def test_saved_network_not_visible(self, provisioner, temp_wpa_config):
        """Test when saved network is NOT visible in scan (immediate broadcast case)."""
        with open(temp_wpa_config, 'w') as f:
            f.write('network={\n    ssid="MyHomeWiFi"\n    psk="secret"\n}')

        # Mock scan to return networks that don't include saved network
        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan:
            mock_scan.return_value = [
                {'ssid': 'Neighbor1', 'signal': -60},
                {'ssid': 'Neighbor2', 'signal': -70},
                {'ssid': 'CoffeeShop', 'signal': -55}
            ]
            assert provisioner._is_saved_network_visible() is False

    def test_no_saved_ssids(self, provisioner, temp_wpa_config):
        """Test when no SSIDs are saved in config."""
        with open(temp_wpa_config, 'w') as f:
            f.write("# Empty config")

        assert provisioner._is_saved_network_visible() is False

    def test_empty_scan_results(self, provisioner, temp_wpa_config):
        """Test when WiFi scan returns no networks."""
        with open(temp_wpa_config, 'w') as f:
            f.write('network={\n    ssid="MyHomeWiFi"\n    psk="secret"\n}')

        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan:
            mock_scan.return_value = []
            assert provisioner._is_saved_network_visible() is False


class TestNeedsProvisioning:
    """Test needs_provisioning method with immediate broadcast behavior."""

    @pytest.fixture
    def temp_wpa_config(self):
        """Create a temporary wpa_supplicant.conf file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    @pytest.fixture
    def config(self, temp_wpa_config):
        """Create test configuration."""
        return ProvisioningConfig(
            enabled=True,
            wpa_supplicant_conf=temp_wpa_config,
            connection_test_timeout=5
        )

    @pytest.fixture
    def provisioner(self, config):
        """Create WiFiProvisioner instance."""
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_needs_provisioning_no_config_file(self, provisioner):
        """Test provisioning needed when config file doesn't exist."""
        provisioner.config.wpa_supplicant_conf = '/nonexistent/path.conf'
        assert provisioner.needs_provisioning() is True

    def test_needs_provisioning_empty_config(self, provisioner, temp_wpa_config):
        """Test provisioning needed when config has no network blocks."""
        with open(temp_wpa_config, 'w') as f:
            f.write("# No networks")
        assert provisioner.needs_provisioning() is True

    def test_needs_provisioning_disabled(self, provisioner):
        """Test provisioning not needed when disabled."""
        provisioner.config.enabled = False
        assert provisioner.needs_provisioning() is False

    def test_needs_provisioning_saved_network_not_visible(self, provisioner, temp_wpa_config):
        """
        Test IMMEDIATE BROADCAST: provisioning needed when saved network is NOT visible.

        This is the key test for the immediate broadcast behavior.
        When the saved SSID is not found in scan results, provisioning should
        start immediately without waiting for connection timeout.
        """
        # Create config with saved network
        with open(temp_wpa_config, 'w') as f:
            f.write('network={\n    ssid="MyHomeWiFi"\n    psk="secret"\n}')

        # Mock scan to return networks that DON'T include saved network
        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan:
            mock_scan.return_value = [
                {'ssid': 'Neighbor1', 'signal': -60},
                {'ssid': 'Neighbor2', 'signal': -70}
            ]
            # Should need provisioning immediately (no connection attempt)
            assert provisioner.needs_provisioning() is True

            # Verify _scan_wifi_networks was called (checking visibility)
            mock_scan.assert_called_once_with(use_cache=False)

    def test_needs_provisioning_saved_network_visible_but_cannot_connect(
        self, provisioner, temp_wpa_config
    ):
        """
        Test provisioning needed when saved network IS visible but cannot connect.

        This tests the fallback to connection test when network is visible.
        """
        with open(temp_wpa_config, 'w') as f:
            f.write('network={\n    ssid="MyHomeWiFi"\n    psk="secret"\n}')

        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan, \
             patch.object(provisioner, '_can_connect') as mock_connect:
            mock_scan.return_value = [{'ssid': 'MyHomeWiFi', 'signal': -45}]
            mock_connect.return_value = False

            assert provisioner.needs_provisioning() is True
            mock_connect.assert_called_once()

    def test_needs_provisioning_connected_successfully(self, provisioner, temp_wpa_config):
        """Test provisioning NOT needed when already connected."""
        with open(temp_wpa_config, 'w') as f:
            f.write('network={\n    ssid="MyHomeWiFi"\n    psk="secret"\n}')

        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan, \
             patch.object(provisioner, '_can_connect') as mock_connect:
            mock_scan.return_value = [{'ssid': 'MyHomeWiFi', 'signal': -45}]
            mock_connect.return_value = True

            assert provisioner.needs_provisioning() is False


class TestImmediateBroadcastBehavior:
    """
    Focused tests for the immediate broadcast behavior when saved network not found.

    These tests verify that the device starts broadcasting the provisioning hotspot
    IMMEDIATELY when the saved WiFi network is not visible, rather than waiting
    for a connection timeout.
    """

    @pytest.fixture
    def temp_wpa_config(self):
        """Create a temporary wpa_supplicant.conf file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    @pytest.fixture
    def config(self, temp_wpa_config):
        """Create test configuration with realistic settings."""
        return ProvisioningConfig(
            enabled=True,
            wpa_supplicant_conf=temp_wpa_config,
            connection_test_timeout=30  # Normal would be 30s
        )

    @pytest.fixture
    def provisioner(self, config):
        """Create WiFiProvisioner instance."""
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_no_connection_attempt_when_network_not_visible(
        self, provisioner, temp_wpa_config
    ):
        """
        Verify that _can_connect is NOT called when saved network is not visible.

        This is critical for the immediate broadcast behavior - we should skip
        the connection attempt entirely if the network isn't even visible.
        """
        with open(temp_wpa_config, 'w') as f:
            f.write('network={\n    ssid="OfficeWiFi"\n    psk="secret"\n}')

        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan, \
             patch.object(provisioner, '_can_connect') as mock_connect:
            # Saved network not in scan results
            mock_scan.return_value = [
                {'ssid': 'RandomNetwork1', 'signal': -55},
                {'ssid': 'RandomNetwork2', 'signal': -65}
            ]

            result = provisioner.needs_provisioning()

            assert result is True
            # Critical: _can_connect should NOT be called
            mock_connect.assert_not_called()

    def test_connection_attempt_only_when_network_visible(
        self, provisioner, temp_wpa_config
    ):
        """
        Verify that _can_connect IS called when saved network is visible.
        """
        with open(temp_wpa_config, 'w') as f:
            f.write('network={\n    ssid="OfficeWiFi"\n    psk="secret"\n}')

        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan, \
             patch.object(provisioner, '_can_connect') as mock_connect:
            # Saved network IS in scan results
            mock_scan.return_value = [
                {'ssid': 'OfficeWiFi', 'signal': -45},  # Our saved network
                {'ssid': 'RandomNetwork', 'signal': -65}
            ]
            mock_connect.return_value = True

            result = provisioner.needs_provisioning()

            assert result is False
            # _can_connect should be called since network is visible
            mock_connect.assert_called_once()

    def test_multiple_saved_networks_one_visible(self, provisioner, temp_wpa_config):
        """
        Test with multiple saved networks where only one is visible.
        Should attempt connection since at least one saved network is found.
        """
        with open(temp_wpa_config, 'w') as f:
            f.write("""
network={
    ssid="HomeWiFi"
    psk="home123"
}
network={
    ssid="OfficeWiFi"
    psk="office123"
}
""")

        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan, \
             patch.object(provisioner, '_can_connect') as mock_connect:
            # Only OfficeWiFi is visible, HomeWiFi is not
            mock_scan.return_value = [
                {'ssid': 'OfficeWiFi', 'signal': -50},
                {'ssid': 'Neighbor', 'signal': -70}
            ]
            mock_connect.return_value = True

            result = provisioner.needs_provisioning()

            assert result is False
            mock_connect.assert_called_once()

    def test_multiple_saved_networks_none_visible(self, provisioner, temp_wpa_config):
        """
        Test with multiple saved networks where NONE are visible.
        Should trigger immediate broadcast without connection attempt.
        """
        with open(temp_wpa_config, 'w') as f:
            f.write("""
network={
    ssid="HomeWiFi"
    psk="home123"
}
network={
    ssid="OfficeWiFi"
    psk="office123"
}
""")

        with patch.object(provisioner, '_scan_wifi_networks') as mock_scan, \
             patch.object(provisioner, '_can_connect') as mock_connect:
            # Neither saved network is visible
            mock_scan.return_value = [
                {'ssid': 'CoffeeShop', 'signal': -55},
                {'ssid': 'PublicWiFi', 'signal': -60}
            ]

            result = provisioner.needs_provisioning()

            assert result is True
            # No connection attempt since no saved network is visible
            mock_connect.assert_not_called()


class TestNmcliFieldParser:
    """Test _parse_nmcli_fields method."""

    @pytest.fixture
    def provisioner(self):
        config = ProvisioningConfig(wpa_supplicant_conf='/tmp/test.conf')
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_simple_fields(self, provisioner):
        """Test parsing simple colon-separated fields."""
        result = provisioner._parse_nmcli_fields('MyNetwork:75:WPA2')
        assert result == ['MyNetwork', '75', 'WPA2']

    def test_escaped_colon_in_ssid(self, provisioner):
        """Test parsing SSID containing a literal colon."""
        result = provisioner._parse_nmcli_fields('My\\:Network:80:WPA2')
        assert result == ['My:Network', '80', 'WPA2']

    def test_empty_fields(self, provisioner):
        """Test parsing with empty fields."""
        result = provisioner._parse_nmcli_fields('HiddenNet::')
        assert result == ['HiddenNet', '', '']

    def test_empty_security(self, provisioner):
        """Test parsing open network with no security."""
        result = provisioner._parse_nmcli_fields('OpenNet:50:')
        assert result == ['OpenNet', '50', '']

    def test_escaped_backslash(self, provisioner):
        """Test parsing escaped backslash."""
        result = provisioner._parse_nmcli_fields('Net\\\\Name:60:WPA2')
        assert result == ['Net\\Name', '60', 'WPA2']


class TestScanWithNmcli:
    """Test _scan_with_nmcli method."""

    @pytest.fixture
    def provisioner(self):
        config = ProvisioningConfig(wpa_supplicant_conf='/tmp/test.conf')
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_nmcli_scan_success(self, provisioner):
        """Test successful nmcli scan with typical output."""
        nmcli_output = (
            "HomeNetwork:85:WPA2\n"
            "OfficeWiFi:60:WPA1 WPA2\n"
            "CoffeeShop:40:WPA2\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = nmcli_output

        with patch('subprocess.run', return_value=mock_result):
            networks = provisioner._scan_with_nmcli()

        assert len(networks) == 3
        assert networks[0]['ssid'] == 'HomeNetwork'
        assert networks[0]['signal'] == -57  # (85/2) - 100
        assert networks[0]['encrypted'] is True
        assert networks[1]['ssid'] == 'OfficeWiFi'
        assert networks[2]['ssid'] == 'CoffeeShop'

    def test_nmcli_scan_filters_own_ap(self, provisioner):
        """Test that our own AP SSID is filtered out."""
        nmcli_output = "TS_12345678:90:WPA2\nRealNetwork:70:WPA2\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = nmcli_output

        with patch('subprocess.run', return_value=mock_result):
            networks = provisioner._scan_with_nmcli()

        assert len(networks) == 1
        assert networks[0]['ssid'] == 'RealNetwork'

    def test_nmcli_scan_filters_empty_and_placeholder(self, provisioner):
        """Test that empty SSIDs and '--' placeholders are filtered."""
        nmcli_output = ":50:WPA2\n--:60:\nRealNetwork:70:WPA2\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = nmcli_output

        with patch('subprocess.run', return_value=mock_result):
            networks = provisioner._scan_with_nmcli()

        assert len(networks) == 1
        assert networks[0]['ssid'] == 'RealNetwork'

    def test_nmcli_scan_open_network(self, provisioner):
        """Test detection of open (unencrypted) network."""
        nmcli_output = "OpenCafe:55:\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = nmcli_output

        with patch('subprocess.run', return_value=mock_result):
            networks = provisioner._scan_with_nmcli()

        assert len(networks) == 1
        assert networks[0]['encrypted'] is False

    def test_nmcli_not_found(self, provisioner):
        """Test graceful fallback when nmcli is not installed."""
        with patch('subprocess.run', side_effect=FileNotFoundError()):
            networks = provisioner._scan_with_nmcli()

        assert networks == []

    def test_nmcli_scan_timeout(self, provisioner):
        """Test graceful handling of scan timeout."""
        import subprocess as sp
        with patch('subprocess.run', side_effect=sp.TimeoutExpired(cmd='nmcli', timeout=15)):
            networks = provisioner._scan_with_nmcli()

        assert networks == []

    def test_nmcli_scan_failure(self, provisioner):
        """Test handling of non-zero return code."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: device not found"

        with patch('subprocess.run', return_value=mock_result):
            networks = provisioner._scan_with_nmcli()

        assert networks == []


class TestScanWithIwlist:
    """Test _scan_with_iwlist method with Cell-boundary parsing."""

    @pytest.fixture
    def provisioner(self):
        config = ProvisioningConfig(wpa_supplicant_conf='/tmp/test.conf')
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_iwlist_standard_output(self, provisioner):
        """Test parsing standard iwlist output (Signal before ESSID)."""
        iwlist_output = """wlan0     Scan completed :
          Cell 01 - Address: 00:11:22:33:44:55
                    Channel:1
                    Frequency:2.412 GHz (Channel 1)
                    Quality=70/70  Signal level=-40 dBm
                    Encryption key:on
                    ESSID:"HomeNetwork"
                    Bit Rates:54 Mb/s
          Cell 02 - Address: 66:77:88:99:AA:BB
                    Channel:6
                    Quality=50/70  Signal level=-65 dBm
                    Encryption key:on
                    ESSID:"OfficeWiFi"
"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = iwlist_output

        with patch('subprocess.run', return_value=mock_result):
            networks = provisioner._scan_with_iwlist()

        assert len(networks) == 2
        assert networks[0]['ssid'] == 'HomeNetwork'
        assert networks[0]['signal'] == -40
        assert networks[0]['encrypted'] is True
        assert networks[1]['ssid'] == 'OfficeWiFi'
        assert networks[1]['signal'] == -65

    def test_iwlist_last_network_captured(self, provisioner):
        """Test that the last network in output is not lost."""
        iwlist_output = """wlan0     Scan completed :
          Cell 01 - Address: 00:11:22:33:44:55
                    Quality=70/70  Signal level=-40 dBm
                    Encryption key:on
                    ESSID:"OnlyNetwork"
"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = iwlist_output

        with patch('subprocess.run', return_value=mock_result):
            networks = provisioner._scan_with_iwlist()

        assert len(networks) == 1
        assert networks[0]['ssid'] == 'OnlyNetwork'

    def test_iwlist_filters_own_ap(self, provisioner):
        """Test that own AP SSID is filtered in iwlist output."""
        iwlist_output = """wlan0     Scan completed :
          Cell 01 - Address: 00:11:22:33:44:55
                    Signal level=-40 dBm
                    ESSID:"TS_12345678"
          Cell 02 - Address: 66:77:88:99:AA:BB
                    Signal level=-50 dBm
                    ESSID:"RealNetwork"
"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = iwlist_output

        with patch('subprocess.run', return_value=mock_result):
            networks = provisioner._scan_with_iwlist()

        assert len(networks) == 1
        assert networks[0]['ssid'] == 'RealNetwork'

    def test_iwlist_hidden_network_skipped(self, provisioner):
        """Test that networks with empty ESSID are skipped."""
        iwlist_output = """wlan0     Scan completed :
          Cell 01 - Address: 00:11:22:33:44:55
                    Signal level=-40 dBm
                    ESSID:""
          Cell 02 - Address: 66:77:88:99:AA:BB
                    Signal level=-50 dBm
                    ESSID:"VisibleNet"
"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = iwlist_output

        with patch('subprocess.run', return_value=mock_result):
            networks = provisioner._scan_with_iwlist()

        assert len(networks) == 1
        assert networks[0]['ssid'] == 'VisibleNet'


class TestScanWifiNetworks:
    """Test _scan_wifi_networks method (nmcli-first with iwlist fallback)."""

    @pytest.fixture
    def provisioner(self):
        config = ProvisioningConfig(wpa_supplicant_conf='/tmp/test.conf')
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_returns_cached_when_available(self, provisioner):
        """Test that cached networks are returned when use_cache=True."""
        provisioner.cached_networks = [
            {'ssid': 'CachedNet', 'signal': -50, 'encrypted': True}
        ]
        result = provisioner._scan_wifi_networks(use_cache=True)
        assert len(result) == 1
        assert result[0]['ssid'] == 'CachedNet'

    def test_ignores_cache_when_use_cache_false(self, provisioner):
        """Test that cache is bypassed when use_cache=False."""
        provisioner.cached_networks = [
            {'ssid': 'CachedNet', 'signal': -50, 'encrypted': True}
        ]
        with patch.object(provisioner, '_scan_with_nmcli') as mock_nmcli:
            mock_nmcli.return_value = [
                {'ssid': 'FreshNet', 'signal': -45, 'encrypted': True}
            ]
            result = provisioner._scan_wifi_networks(use_cache=False)

        assert len(result) == 1
        assert result[0]['ssid'] == 'FreshNet'

    def test_falls_back_to_iwlist_when_nmcli_empty(self, provisioner):
        """Test that iwlist is tried when nmcli returns no results."""
        with patch.object(provisioner, '_scan_with_nmcli', return_value=[]) as mock_nmcli, \
             patch.object(provisioner, '_scan_with_iwlist') as mock_iwlist:
            mock_iwlist.return_value = [
                {'ssid': 'IwlistNet', 'signal': -60, 'encrypted': True}
            ]
            result = provisioner._scan_wifi_networks(use_cache=False)

        mock_nmcli.assert_called_once()
        mock_iwlist.assert_called_once()
        assert len(result) == 1
        assert result[0]['ssid'] == 'IwlistNet'

    def test_deduplicates_networks(self, provisioner):
        """Test that duplicate SSIDs are removed."""
        with patch.object(provisioner, '_scan_with_nmcli') as mock_nmcli:
            mock_nmcli.return_value = [
                {'ssid': 'DupeNet', 'signal': -50, 'encrypted': True},
                {'ssid': 'DupeNet', 'signal': -60, 'encrypted': True},
                {'ssid': 'UniqueNet', 'signal': -70, 'encrypted': False},
            ]
            result = provisioner._scan_wifi_networks(use_cache=False)

        assert len(result) == 2
        ssids = [n['ssid'] for n in result]
        assert ssids.count('DupeNet') == 1

    def test_sorts_by_signal_strength(self, provisioner):
        """Test that networks are sorted strongest-first."""
        with patch.object(provisioner, '_scan_with_nmcli') as mock_nmcli:
            mock_nmcli.return_value = [
                {'ssid': 'Weak', 'signal': -80, 'encrypted': True},
                {'ssid': 'Strong', 'signal': -30, 'encrypted': True},
                {'ssid': 'Medium', 'signal': -55, 'encrypted': True},
            ]
            result = provisioner._scan_wifi_networks(use_cache=False)

        assert result[0]['ssid'] == 'Strong'
        assert result[1]['ssid'] == 'Medium'
        assert result[2]['ssid'] == 'Weak'

    def test_limits_to_15_networks(self, provisioner):
        """Test that results are capped at 15 networks."""
        with patch.object(provisioner, '_scan_with_nmcli') as mock_nmcli:
            mock_nmcli.return_value = [
                {'ssid': f'Net{i}', 'signal': -50 - i, 'encrypted': True}
                for i in range(20)
            ]
            result = provisioner._scan_wifi_networks(use_cache=False)

        assert len(result) == 15


class TestHtmlTemplateDisplay:
    """Test HTML template generation for network display."""

    @pytest.fixture
    def provisioner(self):
        config = ProvisioningConfig(wpa_supplicant_conf='/tmp/test.conf')
        with patch.object(WiFiProvisioner, '_get_device_id', return_value='12345678'):
            return WiFiProvisioner(config=config)

    def test_signal_icon_varies_by_strength(self, provisioner):
        """Test that signal icons differ based on signal strength."""
        networks = [
            {'ssid': 'Strong', 'signal': -40, 'encrypted': True},   # 4 bars
            {'ssid': 'Good', 'signal': -55, 'encrypted': True},     # 3 bars
            {'ssid': 'Fair', 'signal': -65, 'encrypted': True},     # 2 bars
            {'ssid': 'Weak', 'signal': -80, 'encrypted': True},     # 1 bar
        ]
        html = provisioner._get_html_template(networks=networks)
        # 4 bars: ▂▄▆█, 3 bars: ▂▄▆░, 2 bars: ▂▄░░, 1 bar: ▂░░░
        assert '▂▄▆█' in html  # Strong signal
        assert '▂▄▆░' in html  # Good signal
        assert '▂▄░░' in html  # Fair signal
        assert '▂░░░' in html  # Weak signal

    def test_ssid_with_quotes_escaped(self, provisioner):
        """Test that SSIDs with quotes are properly escaped in HTML."""
        networks = [
            {'ssid': "Bob's WiFi", 'signal': -50, 'encrypted': True},
        ]
        html = provisioner._get_html_template(networks=networks)
        # Should use HTML entity for the apostrophe
        assert "Bob&#x27;s WiFi" in html or "Bob&#39;s WiFi" in html
        # Should NOT have unescaped quote in onclick
        assert "selectNetwork('Bob's WiFi')" not in html

    def test_ssid_with_html_chars_escaped(self, provisioner):
        """Test that SSIDs with HTML special chars are escaped."""
        networks = [
            {'ssid': '<script>alert(1)</script>', 'signal': -50, 'encrypted': True},
        ]
        html = provisioner._get_html_template(networks=networks)
        # Should be escaped, not raw HTML
        assert '&lt;script&gt;' in html
        assert '<script>alert(1)</script>' not in html

    def test_ssid_uses_data_attribute(self, provisioner):
        """Test that SSIDs are passed via data-ssid attribute."""
        networks = [
            {'ssid': 'TestNetwork', 'signal': -50, 'encrypted': True},
        ]
        html = provisioner._get_html_template(networks=networks)
        assert 'data-ssid="TestNetwork"' in html
        assert 'this.dataset.ssid' in html

    def test_empty_networks_shows_scanning_message(self, provisioner):
        """Test that empty network list shows scanning message."""
        html = provisioner._get_html_template(networks=[])
        assert 'Scanning for networks...' in html


# Helper function for mocking file open
def mock_open(read_data=''):
    """Create a mock for open() that returns the given data."""
    mock = MagicMock()
    mock.return_value.__enter__ = Mock(return_value=MagicMock(read=Mock(return_value=read_data)))
    mock.return_value.__exit__ = Mock(return_value=False)
    # Also support iteration for line reading
    mock.return_value.__enter__.return_value.__iter__ = Mock(return_value=iter(read_data.split('\n')))
    return mock
