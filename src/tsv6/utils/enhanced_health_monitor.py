#!/usr/bin/env python3
"""
Enhanced System Health Monitor with Display Driver Monitoring

Extends the base health monitor to include display driver monitoring
and recovery for vc4 display driver issues (Issue #40).
"""

import time
import threading
import logging
from typing import Dict, Any, Optional
from .health_monitor import HealthMonitor, HealthMetrics
from ..hardware.display_driver_monitor import DisplayDriverMonitor, get_display_system_info


class EnhancedHealthMonitor(HealthMonitor):
    """Enhanced health monitor with display driver monitoring"""
    
    def __init__(self, check_interval: int = 30, error_recovery=None):
        super().__init__(check_interval)
        
        # Initialize display driver monitor
        self.display_monitor = DisplayDriverMonitor(error_recovery)
        self.display_info = {}
        
        print("🔧 Enhanced Health Monitor initialized with display driver monitoring")
    
    def start_monitoring(self):
        """Start monitoring including display driver health"""
        super().start_monitoring()
        
        # Start display driver monitoring
        self.display_monitor.start_monitoring()
        
        print("📊 Enhanced health monitoring started (including display driver)")
    
    def stop_monitoring(self):
        """Stop all monitoring"""
        super().stop_monitoring()
        
        # Stop display driver monitoring
        if self.display_monitor:
            self.display_monitor.stop_monitoring()
        
        print("🛑 Enhanced health monitoring stopped")
    
    def get_health_metrics(self) -> HealthMetrics:
        """Get enhanced health metrics including display driver status"""
        base_metrics = super().get_health_metrics()
        
        # Add display driver information
        self.display_info = {
            "display_system": get_display_system_info(),
            "display_driver": self.display_monitor.get_health_status() if self.display_monitor else {}
        }
        
        return base_metrics
    
    def get_comprehensive_health_report(self) -> Dict[str, Any]:
        """Get comprehensive health report including display driver status"""
        base_report = super().get_health_summary()
        
        # Add display driver health
        display_health = self.display_monitor.get_health_status() if self.display_monitor else {}
        display_system_info = get_display_system_info()
        
        enhanced_report = {
            **base_report,
            "display_driver": {
                "health_status": display_health,
                "system_info": display_system_info,
                "warnings_detected": display_health.get("warnings_count", 0) > 0,
                "recovery_active": display_health.get("status") in ["recovering", "fallback"],
                "critical_issues": display_health.get("status") == "critical"
            },
            "enhanced_monitoring": True,
            "monitoring_version": "2.0_with_display_driver"
        }
        
        return enhanced_report
    
    def force_display_health_check(self):
        """Force immediate display driver health check"""
        if self.display_monitor:
            return self.display_monitor.force_health_check()
        return {}
    
    def get_display_warnings_summary(self) -> Dict[str, Any]:
        """Get summary of display driver warnings and issues"""
        if not self.display_monitor:
            return {"error": "Display monitor not initialized"}
        
        from ..hardware.display_driver_monitor import check_display_driver_warnings
        
        warning_count, warnings = check_display_driver_warnings()
        health_status = self.display_monitor.get_health_status()
        
        return {
            "recent_warnings_count": warning_count,
            "recent_warnings": warnings[:5],  # Show first 5
            "driver_status": health_status.get("status", "unknown"),
            "gpu_memory_split": health_status.get("gpu_memory_split", 0),
            "display_mode": health_status.get("display_mode", "unknown"),
            "recovery_attempts": health_status.get("recovery_attempts", 0),
            "pipeline_errors": health_status.get("pipeline_errors", 0)
        }
