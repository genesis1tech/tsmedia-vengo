#!/usr/bin/env python3
"""
System Health Monitor for Raspberry Pi

Monitors system health metrics including:
- CPU temperature, usage, and load
- Memory usage and availability
- Disk space and I/O
- Process monitoring
- System uptime and performance

Designed for production IoT devices to detect issues early
and provide comprehensive health reporting.
"""

import psutil
import threading
import time
import json
import logging
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, Callable, List
from pathlib import Path


@dataclass
class HealthMetrics:
    """System health metrics"""
    timestamp: float
    
    # CPU metrics
    cpu_percent: float
    cpu_temp_celsius: float
    cpu_temp_fahrenheit: float
    load_average_1min: float
    load_average_5min: float
    load_average_15min: float
    
    # Memory metrics
    memory_total_mb: float
    memory_available_mb: float
    memory_percent: float
    swap_total_mb: float
    swap_used_mb: float
    swap_percent: float
    
    # Disk metrics
    disk_total_gb: float
    disk_used_gb: float
    disk_free_gb: float
    disk_percent: float
    
    # Network metrics
    network_bytes_sent: int
    network_bytes_recv: int
    network_packets_sent: int
    network_packets_recv: int
    
    # System metrics
    boot_time: float
    uptime_hours: float
    process_count: int
    
    # Health status
    overall_health: str  # "healthy", "warning", "critical"
    alerts: List[str]


@dataclass
class HealthThresholds:
    """Thresholds for health monitoring alerts"""
    cpu_temp_warning_c: float = 78.0
    cpu_temp_critical_c: float = 82.0
    cpu_usage_warning_percent: float = 80.0
    cpu_usage_critical_percent: float = 95.0
    memory_warning_percent: float = 85.0
    memory_critical_percent: float = 95.0
    disk_warning_percent: float = 85.0
    disk_critical_percent: float = 95.0
    load_warning_ratio: float = 2.0  # Ratio of load to CPU count
    load_critical_ratio: float = 4.0


class HealthMonitor:
    """System health monitoring service"""
    
    def __init__(
        self, 
        thresholds: Optional[HealthThresholds] = None,
        check_interval: float = 30.0,
        on_health_update: Optional[Callable[[HealthMetrics], None]] = None,
        on_alert: Optional[Callable[[str, List[str]], None]] = None
    ):
        self.thresholds = thresholds or HealthThresholds()
        self.check_interval = check_interval
        self.on_health_update = on_health_update
        self.on_alert = on_alert
        
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._last_metrics: Optional[HealthMetrics] = None
        self._last_network_stats = None
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        
        print("🏥 Health Monitor initialized")
    
    def start(self):
        """Start health monitoring"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, 
            name="HealthMonitor",
            daemon=True
        )
        self._monitor_thread.start()
        print("🚀 Health monitoring started")
    
    def stop(self):
        """Stop health monitoring"""
        print("🛑 Stopping health monitor...")
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        print("✅ Health monitor stopped")
    
    def get_current_metrics(self) -> HealthMetrics:
        """Get current system health metrics"""
        return self._collect_metrics()
    
    def get_last_metrics(self) -> Optional[HealthMetrics]:
        """Get last collected metrics"""
        return self._last_metrics
    
    def _get_cpu_temperature(self) -> float:
        """Get CPU temperature in Celsius"""
        try:
            # Try multiple methods for getting CPU temperature
            
            # Method 1: Raspberry Pi thermal zone
            thermal_file = Path('/sys/class/thermal/thermal_zone0/temp')
            if thermal_file.exists():
                with open(thermal_file, 'r') as f:
                    temp_millidegrees = int(f.read().strip())
                    return temp_millidegrees / 1000.0
            
            # Method 2: psutil sensors (if available)
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                if temps:
                    # Try common sensor names
                    for sensor_name in ['cpu_thermal', 'coretemp', 'k10temp']:
                        if sensor_name in temps and temps[sensor_name]:
                            return temps[sensor_name][0].current
            
            # Method 3: vcgencmd (Raspberry Pi specific)
            import subprocess
            try:
                result = subprocess.run(
                    ['vcgencmd', 'measure_temp'], 
                    capture_output=True, 
                    text=True, 
                    timeout=2
                )
                if result.returncode == 0:
                    # Output format: temp=XX.X'C
                    temp_str = result.stdout.strip()
                    if 'temp=' in temp_str:
                        temp_val = temp_str.split('=')[1].replace("'C", "")
                        return float(temp_val)
            except:
                pass
                
        except Exception as e:
            self.logger.warning(f"Failed to get CPU temperature: {e}")
        
        return 50.0  # Default fallback temperature
    
    def _collect_metrics(self) -> HealthMetrics:
        """Collect all system health metrics"""
        # Defensive check for module availability (can be None during shutdown)
        if psutil is None:
            raise RuntimeError("psutil unavailable during shutdown")
            
        timestamp = time.time()
        
        # CPU metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_temp_c = self._get_cpu_temperature()
        cpu_temp_f = (cpu_temp_c * 9/5) + 32
        
        # Load average
        load_avg = psutil.getloadavg()
        
        # Memory metrics
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        # Disk metrics (root filesystem)
        disk = psutil.disk_usage('/')
        
        # Network metrics
        network = psutil.net_io_counters()
        
        # System metrics
        boot_time = psutil.boot_time()
        uptime_hours = (timestamp - boot_time) / 3600
        process_count = len(psutil.pids())
        
        # Assess overall health
        alerts = []
        health_status = "healthy"
        
        # Check CPU temperature
        if cpu_temp_c >= self.thresholds.cpu_temp_critical_c:
            alerts.append(f"CRITICAL: CPU temperature {cpu_temp_c:.1f}°C")
            health_status = "critical"
        elif cpu_temp_c >= self.thresholds.cpu_temp_warning_c:
            alerts.append(f"WARNING: CPU temperature {cpu_temp_c:.1f}°C")
            if health_status == "healthy":
                health_status = "warning"
        
        # Check CPU usage
        if cpu_percent >= self.thresholds.cpu_usage_critical_percent:
            alerts.append(f"CRITICAL: CPU usage {cpu_percent:.1f}%")
            health_status = "critical"
        elif cpu_percent >= self.thresholds.cpu_usage_warning_percent:
            alerts.append(f"WARNING: CPU usage {cpu_percent:.1f}%")
            if health_status == "healthy":
                health_status = "warning"
        
        # Check load average (compared to CPU count)
        cpu_count = psutil.cpu_count()
        load_ratio = load_avg[0] / cpu_count if cpu_count > 0 else 0
        
        if load_ratio >= self.thresholds.load_critical_ratio:
            alerts.append(f"CRITICAL: Load average {load_avg[0]:.2f} (ratio: {load_ratio:.2f})")
            health_status = "critical"
        elif load_ratio >= self.thresholds.load_warning_ratio:
            alerts.append(f"WARNING: Load average {load_avg[0]:.2f} (ratio: {load_ratio:.2f})")
            if health_status == "healthy":
                health_status = "warning"
        
        # Check memory usage
        if memory.percent >= self.thresholds.memory_critical_percent:
            alerts.append(f"CRITICAL: Memory usage {memory.percent:.1f}%")
            health_status = "critical"
        elif memory.percent >= self.thresholds.memory_warning_percent:
            alerts.append(f"WARNING: Memory usage {memory.percent:.1f}%")
            if health_status == "healthy":
                health_status = "warning"
        
        # Check disk usage
        disk_percent = (disk.used / disk.total) * 100
        if disk_percent >= self.thresholds.disk_critical_percent:
            alerts.append(f"CRITICAL: Disk usage {disk_percent:.1f}%")
            health_status = "critical"
        elif disk_percent >= self.thresholds.disk_warning_percent:
            alerts.append(f"WARNING: Disk usage {disk_percent:.1f}%")
            if health_status == "healthy":
                health_status = "warning"
        
        return HealthMetrics(
            timestamp=timestamp,
            cpu_percent=cpu_percent,
            cpu_temp_celsius=cpu_temp_c,
            cpu_temp_fahrenheit=cpu_temp_f,
            load_average_1min=load_avg[0],
            load_average_5min=load_avg[1],
            load_average_15min=load_avg[2],
            memory_total_mb=memory.total / (1024 * 1024),
            memory_available_mb=memory.available / (1024 * 1024),
            memory_percent=memory.percent,
            swap_total_mb=swap.total / (1024 * 1024),
            swap_used_mb=swap.used / (1024 * 1024),
            swap_percent=swap.percent,
            disk_total_gb=disk.total / (1024 * 1024 * 1024),
            disk_used_gb=disk.used / (1024 * 1024 * 1024),
            disk_free_gb=disk.free / (1024 * 1024 * 1024),
            disk_percent=disk_percent,
            network_bytes_sent=network.bytes_sent,
            network_bytes_recv=network.bytes_recv,
            network_packets_sent=network.packets_sent,
            network_packets_recv=network.packets_recv,
            boot_time=boot_time,
            uptime_hours=uptime_hours,
            process_count=process_count,
            overall_health=health_status,
            alerts=alerts
        )
    
    def _monitor_loop(self):
        """Main monitoring loop"""
        while not self._stop_event.is_set():
            try:
                # Collect metrics
                metrics = self._collect_metrics()
                self._last_metrics = metrics
                
                # Emit callbacks
                if self.on_health_update:
                    try:
                        self.on_health_update(metrics)
                    except Exception as e:
                        self.logger.error(f"Error in health update callback: {e}")
                
                # Emit alerts if any
                if metrics.alerts and self.on_alert:
                    try:
                        self.on_alert(metrics.overall_health, metrics.alerts)
                    except Exception as e:
                        self.logger.error(f"Error in alert callback: {e}")
                
                # Log critical issues
                if metrics.overall_health == "critical":
                    self.logger.critical(f"System health critical: {', '.join(metrics.alerts)}")
                elif metrics.overall_health == "warning":
                    self.logger.warning(f"System health warning: {', '.join(metrics.alerts)}")
                
                # Wait for next check
                self._stop_event.wait(self.check_interval)
                
            except (KeyError, NameError, AttributeError, RuntimeError) as e:
                # Silently handle shutdown-related errors
                if isinstance(e, (NameError, AttributeError, RuntimeError)):
                    break  # Shutdown in progress, exit gracefully
                self.logger.error(f"Error in health monitoring loop: {type(e).__name__}: {e}")
                self._stop_event.wait(5)  # Brief pause on error
    
    def get_health_summary(self) -> Dict[str, Any]:
        """Get a summary of current system health"""
        metrics = self.get_current_metrics()
        
        return {
            'status': metrics.overall_health,
            'alerts': metrics.alerts,
            'cpu': {
                'usage_percent': metrics.cpu_percent,
                'temperature_c': metrics.cpu_temp_celsius,
                'temperature_f': metrics.cpu_temp_fahrenheit,
                'load_1min': metrics.load_average_1min
            },
            'memory': {
                'usage_percent': metrics.memory_percent,
                'available_mb': metrics.memory_available_mb,
                'total_mb': metrics.memory_total_mb
            },
            'disk': {
                'usage_percent': metrics.disk_percent,
                'free_gb': metrics.disk_free_gb,
                'total_gb': metrics.disk_total_gb
            },
            'system': {
                'uptime_hours': metrics.uptime_hours,
                'process_count': metrics.process_count
            }
        }
    
    def to_dict(self, metrics: Optional[HealthMetrics] = None) -> Dict[str, Any]:
        """Convert metrics to dictionary"""
        if metrics is None:
            metrics = self.get_current_metrics()
        return asdict(metrics)
