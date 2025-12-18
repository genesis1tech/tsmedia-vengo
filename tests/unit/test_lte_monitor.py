#!/usr/bin/env python3
"""
Unit tests for LTE Monitor and Connectivity Manager

Tests the LTE network monitoring and WiFi/LTE failover functionality.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import threading
import time


class TestLTEMonitorConfig:
    """Test LTEMonitorConfig dataclass"""

    def test_default_config(self):
        """Test default configuration values"""
        from src.tsv6.utils.lte_monitor import LTEMonitorConfig

        config = LTEMonitorConfig()

        assert config.check_interval_secs == 30.0
        assert config.signal_weak_threshold_rssi == 10
        assert config.signal_critical_threshold_rssi == 5
        assert config.ping_target == "8.8.8.8"
        assert config.soft_recovery_threshold == 2
        assert config.intermediate_recovery_threshold == 4
        assert config.hard_recovery_threshold == 6
        assert config.critical_escalation_threshold == 10

    def test_custom_config(self):
        """Test custom configuration"""
        from src.tsv6.utils.lte_monitor import LTEMonitorConfig

        config = LTEMonitorConfig(
            check_interval_secs=15.0,
            signal_weak_threshold_rssi=15,
            ping_target="1.1.1.1"
        )

        assert config.check_interval_secs == 15.0
        assert config.signal_weak_threshold_rssi == 15
        assert config.ping_target == "1.1.1.1"


class TestLTERecoveryStage:
    """Test LTERecoveryStage tracking"""

    def test_initial_state(self):
        """Test initial recovery state"""
        from src.tsv6.utils.lte_monitor import LTERecoveryStage

        stage = LTERecoveryStage()

        assert stage.consecutive_failures == 0
        assert stage.soft_attempts == 0
        assert stage.intermediate_attempts == 0
        assert stage.hard_attempts == 0
        assert stage.current_stage == "none"

    def test_reset(self):
        """Test recovery state reset"""
        from src.tsv6.utils.lte_monitor import LTERecoveryStage

        stage = LTERecoveryStage()
        stage.consecutive_failures = 5
        stage.soft_attempts = 2
        stage.current_stage = "soft"

        stage.reset()

        assert stage.consecutive_failures == 0
        assert stage.soft_attempts == 0
        assert stage.current_stage == "none"


class TestLTEMonitor:
    """Test LTE monitor functionality"""

    @pytest.fixture
    def mock_lte_controller(self):
        """Create a mock LTE controller"""
        controller = Mock()
        controller.is_connected.return_value = True
        controller.get_signal_quality.return_value = (20, 0)
        controller.get_signal_dbm.return_value = -73
        controller.get_network_status.return_value = {
            'operator': 'Hologram',
            'ip_address': '10.170.1.100',
            'data_connected': True,
        }
        return controller

    def test_initialization(self, mock_lte_controller):
        """Test LTE monitor initialization"""
        from src.tsv6.utils.lte_monitor import LTEMonitor, LTEMonitorConfig

        config = LTEMonitorConfig()
        monitor = LTEMonitor(
            lte_controller=mock_lte_controller,
            config=config
        )

        assert monitor.controller == mock_lte_controller
        assert monitor.cfg.check_interval_secs == 30.0

    def test_get_recovery_status(self, mock_lte_controller):
        """Test getting recovery status"""
        from src.tsv6.utils.lte_monitor import LTEMonitor, LTEMonitorConfig

        monitor = LTEMonitor(
            lte_controller=mock_lte_controller,
            config=LTEMonitorConfig()
        )

        status = monitor.get_recovery_status()

        assert 'current_stage' in status
        assert 'consecutive_failures' in status
        assert 'soft_attempts' in status
        assert 'is_connected' in status


class TestConnectivityMode:
    """Test ConnectivityMode enum"""

    def test_connectivity_modes(self):
        """Test all connectivity modes are defined"""
        from src.tsv6.utils.connectivity_manager import ConnectivityMode

        assert ConnectivityMode.WIFI_ONLY.value == "wifi_only"
        assert ConnectivityMode.LTE_ONLY.value == "lte_only"
        assert ConnectivityMode.WIFI_PRIMARY_LTE_BACKUP.value == "wifi_primary_lte_backup"
        assert ConnectivityMode.LTE_PRIMARY_WIFI_BACKUP.value == "lte_primary_wifi_backup"


class TestConnectionType:
    """Test ConnectionType enum"""

    def test_connection_types(self):
        """Test all connection types are defined"""
        from src.tsv6.utils.connectivity_manager import ConnectionType

        assert ConnectionType.NONE.value == "none"
        assert ConnectionType.WIFI.value == "wifi"
        assert ConnectionType.LTE.value == "lte"


class TestConnectivityManagerConfig:
    """Test ConnectivityManagerConfig dataclass"""

    def test_default_config(self):
        """Test default connectivity manager configuration"""
        from src.tsv6.utils.connectivity_manager import ConnectivityManagerConfig, ConnectivityMode

        config = ConnectivityManagerConfig()

        assert config.mode == ConnectivityMode.LTE_PRIMARY_WIFI_BACKUP
        assert config.failover_timeout_secs == 60.0
        assert config.failback_check_interval_secs == 300.0
        assert config.failback_stability_secs == 30.0


class TestConnectivityManager:
    """Test ConnectivityManager functionality"""

    @pytest.fixture
    def mock_wifi_monitor(self):
        """Create a mock WiFi monitor"""
        monitor = Mock()
        monitor.on_status = None
        monitor.on_disconnect = None
        monitor.on_reconnect = None
        return monitor

    @pytest.fixture
    def mock_lte_monitor(self):
        """Create a mock LTE monitor"""
        monitor = Mock()
        monitor.on_status = None
        monitor.on_disconnect = None
        monitor.on_reconnect = None
        return monitor

    def test_initialization_lte_primary(self, mock_wifi_monitor, mock_lte_monitor):
        """Test initialization with LTE primary mode"""
        from src.tsv6.utils.connectivity_manager import (
            ConnectivityManager, ConnectivityManagerConfig, ConnectivityMode, ConnectionType
        )

        config = ConnectivityManagerConfig(mode=ConnectivityMode.LTE_PRIMARY_WIFI_BACKUP)
        manager = ConnectivityManager(
            config=config,
            wifi_monitor=mock_wifi_monitor,
            lte_monitor=mock_lte_monitor
        )

        assert manager._primary == ConnectionType.LTE
        assert manager._backup == ConnectionType.WIFI
        assert manager._active_connection == ConnectionType.NONE

    def test_initialization_wifi_primary(self, mock_wifi_monitor, mock_lte_monitor):
        """Test initialization with WiFi primary mode"""
        from src.tsv6.utils.connectivity_manager import (
            ConnectivityManager, ConnectivityManagerConfig, ConnectivityMode, ConnectionType
        )

        config = ConnectivityManagerConfig(mode=ConnectivityMode.WIFI_PRIMARY_LTE_BACKUP)
        manager = ConnectivityManager(
            config=config,
            wifi_monitor=mock_wifi_monitor,
            lte_monitor=mock_lte_monitor
        )

        assert manager._primary == ConnectionType.WIFI
        assert manager._backup == ConnectionType.LTE

    def test_initialization_lte_only(self, mock_lte_monitor):
        """Test initialization with LTE only mode"""
        from src.tsv6.utils.connectivity_manager import (
            ConnectivityManager, ConnectivityManagerConfig, ConnectivityMode, ConnectionType
        )

        config = ConnectivityManagerConfig(mode=ConnectivityMode.LTE_ONLY)
        manager = ConnectivityManager(
            config=config,
            wifi_monitor=None,
            lte_monitor=mock_lte_monitor
        )

        assert manager._primary == ConnectionType.LTE
        assert manager._backup is None

    def test_is_connected_false_initially(self, mock_wifi_monitor, mock_lte_monitor):
        """Test is_connected returns False initially"""
        from src.tsv6.utils.connectivity_manager import ConnectivityManager, ConnectivityManagerConfig

        manager = ConnectivityManager(
            config=ConnectivityManagerConfig(),
            wifi_monitor=mock_wifi_monitor,
            lte_monitor=mock_lte_monitor
        )

        assert manager.is_connected() == False

    def test_is_metered_with_lte(self, mock_wifi_monitor, mock_lte_monitor):
        """Test is_metered returns True when on LTE"""
        from src.tsv6.utils.connectivity_manager import (
            ConnectivityManager, ConnectivityManagerConfig, ConnectionType
        )

        manager = ConnectivityManager(
            config=ConnectivityManagerConfig(),
            wifi_monitor=mock_wifi_monitor,
            lte_monitor=mock_lte_monitor
        )

        manager._active_connection = ConnectionType.LTE
        assert manager.is_metered() == True

        manager._active_connection = ConnectionType.WIFI
        assert manager.is_metered() == False

    def test_get_status(self, mock_wifi_monitor, mock_lte_monitor):
        """Test get_status returns comprehensive status"""
        from src.tsv6.utils.connectivity_manager import ConnectivityManager, ConnectivityManagerConfig

        manager = ConnectivityManager(
            config=ConnectivityManagerConfig(),
            wifi_monitor=mock_wifi_monitor,
            lte_monitor=mock_lte_monitor
        )

        status = manager.get_status()

        assert 'mode' in status
        assert 'active_connection' in status
        assert 'is_connected' in status
        assert 'is_metered' in status
        assert 'wifi' in status
        assert 'lte' in status
        assert 'primary' in status
        assert 'backup' in status

    def test_get_active_connection_str(self, mock_wifi_monitor, mock_lte_monitor):
        """Test get_active_connection_str returns correct string"""
        from src.tsv6.utils.connectivity_manager import (
            ConnectivityManager, ConnectivityManagerConfig, ConnectionType
        )

        manager = ConnectivityManager(
            config=ConnectivityManagerConfig(),
            wifi_monitor=mock_wifi_monitor,
            lte_monitor=mock_lte_monitor
        )

        assert manager.get_active_connection_str() == "none"

        manager._active_connection = ConnectionType.LTE
        assert manager.get_active_connection_str() == "lte"

        manager._active_connection = ConnectionType.WIFI
        assert manager.get_active_connection_str() == "wifi"


class TestLTEConfigIntegration:
    """Test LTEConfig integration with config module"""

    def test_lte_config_dataclass(self):
        """Test LTEConfig dataclass exists and has correct defaults"""
        from src.tsv6.config.config import LTEConfig

        config = LTEConfig()

        assert config.enabled == False
        assert config.apn == "hologram"
        assert config.force_lte == True
        assert config.enable_roaming == True
        assert config.rndis_mode == True
        assert config.power_gpio == 6

    def test_connectivity_config_dataclass(self):
        """Test ConnectivityConfig dataclass exists and has correct defaults"""
        from src.tsv6.config.config import ConnectivityConfig

        config = ConnectivityConfig()

        assert config.mode == "lte_primary_wifi_backup"
        assert config.failover_timeout_secs == 60.0
        assert config.failback_stability_secs == 30.0

    def test_main_config_includes_lte(self):
        """Test main Config class includes LTE configuration"""
        from src.tsv6.config.config import Config

        config = Config()

        assert hasattr(config, 'lte')
        assert hasattr(config, 'connectivity')
        assert config.lte.apn == "hologram"
        assert config.connectivity.mode == "lte_primary_wifi_backup"
