#!/usr/bin/env python3
"""
Production Configuration Management

Enhanced configuration system for production IoT deployment with:
- Environment-based configuration
- Secrets management
- Deployment-specific settings
- Configuration validation
- Runtime configuration updates
"""

import os
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum
import socket
from ..utils.filesystem_ops import atomic_write_json


class DeploymentEnvironment(Enum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass
class NetworkConfig:
    """Network configuration for production"""
    wifi_interface: str = "wlan0"
    wifi_check_interval: int = 10
    wifi_recovery_attempts: int = 3
    dns_servers: List[str] = None
    ntp_servers: List[str] = None
    
    def __post_init__(self):
        if self.dns_servers is None:
            self.dns_servers = ["8.8.8.8", "1.1.1.1"]
        if self.ntp_servers is None:
            self.ntp_servers = ["pool.ntp.org", "time.nist.gov"]


@dataclass
class MonitoringConfig:
    """System monitoring configuration"""
    health_check_interval: int = 30
    network_monitor_interval: int = 10
    error_recovery_enabled: bool = True
    log_level: str = "INFO"
    log_rotation_size: int = 10 * 1024 * 1024  # 10MB
    log_retention_days: int = 7
    metrics_retention_hours: int = 48


@dataclass
class SecurityConfig:
    """Security configuration"""
    enable_ssh: bool = False
    enable_firewall: bool = True
    auto_security_updates: bool = True
    fail2ban_enabled: bool = True
    certificate_auto_renewal: bool = True


@dataclass
class PerformanceConfig:
    """Performance tuning configuration"""
    cpu_governor: str = "ondemand"
    gpu_memory_split: int = 64
    swap_size_mb: int = 1024
    io_scheduler: str = "deadline"
    network_buffer_size: int = 65536


@dataclass
class SleepConfig:
    """Sleep mode configuration for power saving"""
    enabled: bool = True
    sleep_time: str = "22:30"  # Time to enter sleep mode (24-hour format HH:MM) 10:30 PM
    wake_time: str = "06:00"  # Time to wake from sleep mode (24-hour format HH:MM) 6:00 AM
    tsv6_service_name: str = "tsv6.service"  # Service to manage during sleep
    publish_status_on_sleep: bool = True  # Publish a status message when entering sleep
    disconnect_aws_on_sleep: bool = True  # Disconnect from AWS to save energy


class ProductionConfigManager:
    """Production configuration management system"""

    def __init__(self, config_dir: Optional[Path] = None):
        # Determine environment first to set appropriate certificate directory
        self.environment = self._detect_environment()

        if config_dir is None:
            if self.environment == DeploymentEnvironment.PRODUCTION:
                # Production uses secure system directory outside repository
                config_dir = Path('/etc/tsv6/certs')
            else:
                # Development/testing uses project's assets/certs directory
                # Get project root (3 levels up from this file)
                project_root = Path(__file__).parent.parent.parent.parent
                config_dir = project_root / "assets" / "certs"
        self.config_dir = config_dir
        self.runtime_config_file = self.config_dir / "runtime_config.json"
        self.secrets_file = self.config_dir / "secrets.json"

        # Load configurations
        self.network_config = NetworkConfig()
        self.monitoring_config = MonitoringConfig()
        self.security_config = SecurityConfig()
        self.performance_config = PerformanceConfig()
        self.sleep_config = SleepConfig()

        # Runtime settings
        self.device_info = self._get_device_info()

        # Load from files
        self._load_configuration()

        print(f"🔧 Production config loaded for {self.environment.value} environment")
    
    def _detect_environment(self) -> DeploymentEnvironment:
        """Detect the deployment environment"""
        env = os.getenv("TSV6_ENVIRONMENT", "production").lower()
        
        try:
            return DeploymentEnvironment(env)
        except ValueError:
            print(f"⚠ Unknown environment '{env}', defaulting to production")
            return DeploymentEnvironment.PRODUCTION
    
    def _get_device_info(self) -> Dict[str, Any]:
        """Get device hardware and system information"""
        device_info = {
            "hostname": socket.gethostname(),
            "platform": "raspberry-pi",
            "architecture": self._get_architecture(),
            "serial_number": self._get_serial_number(),
            "mac_address": self._get_mac_address(),
            "os_version": self._get_os_version(),
            "kernel_version": self._get_kernel_version(),
            "memory_total_mb": self._get_total_memory(),
            "disk_total_gb": self._get_total_disk(),
            "cpu_cores": self._get_cpu_cores(),
        }
        
        return device_info
    
    def _get_architecture(self) -> str:
        """Get system architecture"""
        try:
            import platform
            return platform.machine()
        except:
            return "unknown"
    
    def _get_serial_number(self) -> str:
        """Get Raspberry Pi serial number"""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        return line.split(':')[1].strip()[-8:].upper()
        except:
            pass
        return "00000000"
    
    def _get_mac_address(self) -> str:
        """Get WiFi MAC address"""
        try:
            import subprocess
            result = subprocess.run(['cat', '/sys/class/net/wlan0/address'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except:
            pass
        return "00:00:00:00:00:00"
    
    def _get_os_version(self) -> str:
        """Get OS version"""
        try:
            with open('/etc/os-release', 'r') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        return line.split('=')[1].strip().replace('"', '')
        except:
            pass
        return "Unknown"
    
    def _get_kernel_version(self) -> str:
        """Get kernel version"""
        try:
            import subprocess
            result = subprocess.run(['uname', '-r'], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except:
            pass
        return "unknown"
    
    def _get_total_memory(self) -> int:
        """Get total memory in MB"""
        try:
            import psutil
            return int(psutil.virtual_memory().total / (1024 * 1024))
        except:
            return 0
    
    def _get_total_disk(self) -> float:
        """Get total disk space in GB"""
        try:
            import psutil
            return round(psutil.disk_usage('/').total / (1024 * 1024 * 1024), 1)
        except:
            return 0.0
    
    def _get_cpu_cores(self) -> int:
        """Get CPU core count"""
        try:
            import psutil
            return psutil.cpu_count()
        except:
            return 1
    
    def _load_configuration(self):
        """Load configuration from files and environment variables"""
        # Load runtime configuration
        if self.runtime_config_file.exists():
            try:
                with open(self.runtime_config_file, 'r') as f:
                    runtime_config = json.load(f)
                    self._apply_runtime_config(runtime_config)
                print("✅ Runtime configuration loaded")
            except Exception as e:
                print(f"⚠ Failed to load runtime config: {e}")
        
        # Load environment-specific overrides
        self._load_environment_overrides()
        
        # Validate configuration
        self._validate_configuration()
    
    def _apply_runtime_config(self, config: Dict[str, Any]):
        """Apply runtime configuration overrides"""
        # Network config
        if "network" in config:
            net_config = config["network"]
            for key, value in net_config.items():
                if hasattr(self.network_config, key):
                    setattr(self.network_config, key, value)
        
        # Monitoring config
        if "monitoring" in config:
            mon_config = config["monitoring"]
            for key, value in mon_config.items():
                if hasattr(self.monitoring_config, key):
                    setattr(self.monitoring_config, key, value)
        
        # Security config
        if "security" in config:
            sec_config = config["security"]
            for key, value in sec_config.items():
                if hasattr(self.security_config, key):
                    setattr(self.security_config, key, value)

        # Performance config
        if "performance" in config:
            perf_config = config["performance"]
            for key, value in perf_config.items():
                if hasattr(self.performance_config, key):
                    setattr(self.performance_config, key, value)

        # Sleep config
        if "sleep" in config:
            sleep_config = config["sleep"]
            for key, value in sleep_config.items():
                if hasattr(self.sleep_config, key):
                    setattr(self.sleep_config, key, value)
    
    def _load_environment_overrides(self):
        """Load environment-specific configuration overrides"""
        # Environment-based configuration
        env_overrides = {
            DeploymentEnvironment.DEVELOPMENT: {
                "monitoring": {"log_level": "DEBUG", "health_check_interval": 10},
                "security": {"enable_ssh": True, "enable_firewall": False}
            },
            DeploymentEnvironment.TESTING: {
                "monitoring": {"log_level": "DEBUG", "health_check_interval": 15},
                "security": {"enable_ssh": True}
            },
            DeploymentEnvironment.STAGING: {
                "monitoring": {"log_level": "INFO", "health_check_interval": 30},
                "security": {"enable_ssh": True}
            },
            DeploymentEnvironment.PRODUCTION: {
                "monitoring": {"log_level": "INFO", "health_check_interval": 30},
                "security": {"enable_ssh": False, "enable_firewall": True}
            }
        }
        
        if self.environment in env_overrides:
            self._apply_runtime_config(env_overrides[self.environment])
    
    def _check_tmpfs_available(self) -> bool:
        """Check if tmpfs is available for /var/log (SD card protection)"""
        try:
            # Check if /var/log is mounted as tmpfs
            import subprocess
            result = subprocess.run(
                ['mount'], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
            
            # Look for tmpfs mount on /var/log
            for line in result.stdout.split('\n'):
                if 'tmpfs' in line and '/var/log' in line:
                    return True
                    
            return False
            
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            # If we can't check, assume tmpfs is not available
            return False
    
    def _validate_configuration(self):
        """Validate configuration settings"""
        issues = []
        
        # Validate network settings
        if self.network_config.wifi_check_interval < 5:
            issues.append("WiFi check interval should be at least 5 seconds")
        
        # Validate monitoring settings
        if self.monitoring_config.health_check_interval < 10:
            issues.append("Health check interval should be at least 10 seconds")
        
        # Validate log level
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.monitoring_config.log_level not in valid_log_levels:
            issues.append(f"Invalid log level: {self.monitoring_config.log_level}")
        
        if issues:
            print("⚠ Configuration validation issues:")
            for issue in issues:
                print(f"  - {issue}")
    
    def get_aws_config(self) -> Dict[str, Any]:
        """Get AWS configuration using project assets/certs for certificates in all environments"""
        thing_name_prefix = "TS_"
        # Always use TS_ prefix regardless of environment
        
        return {
            "endpoint": os.getenv("AWS_IOT_ENDPOINT", "a13t5p0hkhkxql-ats.iot.us-east-1.amazonaws.com"),
            "thing_name": f"{thing_name_prefix}{self.device_info['serial_number']}",
            "region": os.getenv("AWS_REGION", "us-east-1"),
            "cert_path": (Path(__file__).parent.parent.parent.parent / "assets" / "certs" / "aws_cert_crt.pem"),
            "key_path": (Path(__file__).parent.parent.parent.parent / "assets" / "certs" / "aws_cert_private.pem"),
            "ca_path": (Path(__file__).parent.parent.parent.parent / "assets" / "certs" / "aws_cert_ca.pem"),
            "keep_alive_secs": 60,
            "connection_timeout": 30,
            "max_reconnect_attempts": 5
        }
    
    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration with SD card wear prevention (Issue #20)"""
        # Check if tmpfs is mounted for /var/log (SD card protection)
        tmpfs_available = self._check_tmpfs_available()
        
        if tmpfs_available:
            # Use tmpfs-based logging for SD card protection
            log_dir = Path("/var/log/tsv6")
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                # Ensure proper permissions for tmpfs
                import os
                os.chown(str(log_dir), os.getuid(), os.getgid())
            except (PermissionError, OSError):
                # If tmpfs fails, fall back to user directory
                log_dir = Path.home() / "logs" / "tsv6"
                log_dir.mkdir(parents=True, exist_ok=True)
                print(f"⚠ tmpfs /var/log not accessible, using fallback: {log_dir}")
        else:
            # Legacy behavior - direct SD card logging (not recommended)
            print(f"⚠ WARNING: tmpfs not detected for /var/log - SD card wear possible!")
            print(f"   Run: sudo scripts/setup_sd_card_protection.sh")
            
            try:
                log_dir = Path("/var/log/tsv6")
                log_dir.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                # Fallback to user home directory
                log_dir = Path.home() / "logs" / "tsv6"
                log_dir.mkdir(parents=True, exist_ok=True)
                print(f"⚠ Using fallback log directory: {log_dir}")
        
        # Add diagnostic information about SD card protection
        protection_status = "tmpfs-protected" if tmpfs_available else "SD-card-direct"
        print(f"📊 Logging mode: {protection_status} (Issue #20 SD card protection)")
        print(f"📁 Log directory: {log_dir}")
        
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
                },
                "detailed": {
                    "format": "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s"
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": self.monitoring_config.log_level,
                    "formatter": "standard",
                    "stream": "ext://sys.stdout"
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": self.monitoring_config.log_level,
                    "formatter": "detailed",
                    "filename": str(log_dir / "tsv6.log"),
                    "maxBytes": self.monitoring_config.log_rotation_size,
                    # Reduced backup count for tmpfs (SD card protection)
                    "backupCount": 2 if tmpfs_available else 5
                },
                "error_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "ERROR",
                    "formatter": "detailed",
                    "filename": str(log_dir / "tsv6_errors.log"),
                    "maxBytes": self.monitoring_config.log_rotation_size,
                    # Reduced backup count for tmpfs (SD card protection)
                    "backupCount": 2 if tmpfs_available else 3
                }
            },
            "loggers": {
                "": {
                    "handlers": ["console", "file", "error_file"],
                    "level": self.monitoring_config.log_level,
                    "propagate": False
                }
            }
        }
    

    def get_ota_config(self) -> Dict[str, Any]:
        """Get OTA configuration with fallback defaults and SD card protection"""
        # Use /tmp for staging if tmpfs is available, otherwise use dedicated directory
        tmpfs_available = self._check_tmpfs_available()
        
        if tmpfs_available:
            # tmpfs /tmp available - safe to use for staging
            staging_dir = "/tmp/ota_staging"
            backup_dir = "/tmp/ota_backup"
        else:
            # No tmpfs - use dedicated directories to minimize SD card writes
            staging_dir = "/home/pi/ota_staging"
            backup_dir = "/home/pi/ota_backup"
            
        return {
            "ENABLED": True,
            "AUTO_INSTALL": True,
            "STAGING_DIR": staging_dir,
            "BACKUP_DIR": backup_dir,
            "VERIFY_SIGNATURES": True,
            "TMPFS_PROTECTED": tmpfs_available
        }

    def get_lte_config(self) -> Dict[str, Any]:
        """Get LTE configuration from environment variables with defaults for Hologram.io"""
        return {
            "enabled": os.getenv("TSV6_LTE_ENABLED", "false").lower() in ("true", "1", "yes"),
            "port": os.getenv("TSV6_LTE_PORT", ""),  # Auto-detect if empty
            "baudrate": int(os.getenv("TSV6_LTE_BAUD", "115200")),
            "apn": os.getenv("TSV6_LTE_APN", "hologram"),
            "apn_username": os.getenv("TSV6_LTE_APN_USER", ""),
            "apn_password": os.getenv("TSV6_LTE_APN_PASS", ""),
            "force_lte": os.getenv("TSV6_LTE_FORCE_LTE", "true").lower() in ("true", "1", "yes"),
            "enable_roaming": os.getenv("TSV6_LTE_ROAMING", "true").lower() in ("true", "1", "yes"),
            "rndis_mode": os.getenv("TSV6_LTE_RNDIS", "true").lower() in ("true", "1", "yes"),
            "power_gpio": int(os.getenv("TSV6_LTE_POWER_GPIO", "6")),
            "use_gpio_power": os.getenv("TSV6_LTE_USE_GPIO", "true").lower() in ("true", "1", "yes"),
            "check_interval_secs": float(os.getenv("TSV6_LTE_CHECK_INTERVAL", "30.0")),
            "signal_weak_threshold": int(os.getenv("TSV6_LTE_WEAK_SIGNAL", "10")),
            "signal_critical_threshold": int(os.getenv("TSV6_LTE_CRITICAL_SIGNAL", "5")),
            "keepalive_interval_secs": int(os.getenv("TSV6_LTE_KEEPALIVE", "30")),
        }

    def get_connectivity_config(self) -> Dict[str, Any]:
        """Get connectivity mode configuration from environment variables"""
        return {
            "mode": os.getenv("TSV6_CONNECTIVITY_MODE", "lte_primary_wifi_backup"),
            "failover_timeout_secs": float(os.getenv("TSV6_FAILOVER_TIMEOUT", "60.0")),
            "failback_check_interval_secs": float(os.getenv("TSV6_FAILBACK_INTERVAL", "300.0")),
            "failback_stability_secs": float(os.getenv("TSV6_FAILBACK_STABILITY", "30.0")),
            "status_report_interval_secs": float(os.getenv("TSV6_STATUS_INTERVAL", "60.0")),
        }

    def save_runtime_config(self):
        """Save current runtime configuration"""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)

            runtime_config = {
                "network": asdict(self.network_config),
                "monitoring": asdict(self.monitoring_config),
                "security": asdict(self.security_config),
                "performance": asdict(self.performance_config),
                "sleep": asdict(self.sleep_config),
                "device_info": self.device_info,
                "last_updated": time.time(),
                "environment": self.environment.value
            }

            # Use atomic write to prevent corruption on power loss (Issue #21)
            if not atomic_write_json(self.runtime_config_file, runtime_config, indent=2):
                raise Exception("Atomic write failed")

            print("✅ Runtime configuration saved")

        except Exception as e:
            print(f"❌ Failed to save runtime config: {e}")

    def get_full_config(self) -> Dict[str, Any]:
        """Get complete configuration as dictionary"""
        return {
            "environment": self.environment.value,
            "device_info": self.device_info,
            "network": asdict(self.network_config),
            "monitoring": asdict(self.monitoring_config),
            "security": asdict(self.security_config),
            "performance": asdict(self.performance_config),
            "sleep": asdict(self.sleep_config),
            "aws": self.get_aws_config(),
            "lte": self.get_lte_config(),
            "connectivity": self.get_connectivity_config()
        }
    
    def update_config(self, updates: Dict[str, Any]):
        """Update configuration at runtime"""
        try:
            self._apply_runtime_config(updates)
            self.save_runtime_config()
            print("✅ Configuration updated")
        except Exception as e:
            print(f"❌ Failed to update configuration: {e}")
    
    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.environment == DeploymentEnvironment.PRODUCTION
    
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment == DeploymentEnvironment.DEVELOPMENT
