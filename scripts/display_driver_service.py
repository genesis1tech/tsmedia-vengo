#!/usr/bin/env python3
"""
Display Driver Monitoring Service

Background service for monitoring vc4 display driver health
and implementing automatic recovery mechanisms.

Addresses issue #40: Display driver causing kernel warnings and system instability.
"""

import sys
import os
import time
import signal
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.tsv6.hardware.display_driver_monitor import DisplayDriverMonitor
from src.tsv6.utils.error_recovery import ErrorRecoverySystem


class DisplayDriverService:
    """Background service for display driver monitoring"""
    
    def __init__(self):
        self.running = False
        self.display_monitor = None
        self.error_recovery = None
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('/tmp/display_driver_service.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        
        print("🖥️ Display Driver Service initialized")
    
    def _handle_signal(self, signum, frame):
        """Handle shutdown signals"""
        print(f"📡 Received signal {signum}, shutting down...")
        self.stop()
    
    def start(self):
        """Start the display driver monitoring service"""
        if self.running:
            return
        
        try:
            print("🚀 Starting Display Driver Monitoring Service...")
            
            # Initialize error recovery system
            self.error_recovery = ErrorRecoverySystem()
            
            # Initialize display driver monitor
            self.display_monitor = DisplayDriverMonitor(self.error_recovery)
            
            # Start monitoring
            self.display_monitor.start_monitoring()
            
            self.running = True
            
            # Log startup info
            health_status = self.display_monitor.get_health_status()
            self.logger.info(f"Display driver service started - Status: {health_status['status']}")
            
            print("✅ Display Driver Monitoring Service started successfully")
            print(f"   Status: {health_status['status']}")
            print(f"   GPU Memory: {health_status['gpu_memory_split']}MB")
            print(f"   Display Mode: {health_status['display_mode']}")
            
            # Main service loop
            self._run_service_loop()
            
        except Exception as e:
            self.logger.error(f"Failed to start display driver service: {e}")
            print(f"❌ Service startup failed: {e}")
            self.stop()
    
    def _run_service_loop(self):
        """Main service loop"""
        last_status_report = 0
        
        while self.running:
            try:
                current_time = time.time()
                
                # Periodic status reporting (every 10 minutes)
                if current_time - last_status_report >= 600:
                    self._report_status()
                    last_status_report = current_time
                
                # Sleep for service interval
                time.sleep(30)
                
            except KeyboardInterrupt:
                print("\n🛑 Service interrupted by user")
                break
            except Exception as e:
                self.logger.error(f"Error in service loop: {e}")
                time.sleep(60)  # Wait longer on errors
    
    def _report_status(self):
        """Report periodic status"""
        try:
            if self.display_monitor:
                health_status = self.display_monitor.get_health_status()
                
                status_msg = (
                    f"Display Driver Status: {health_status['status']} | "
                    f"Warnings: {health_status['warnings_count']} | "
                    f"Recovery Attempts: {health_status['recovery_attempts']}"
                )
                
                self.logger.info(status_msg)
                
                # Report to error recovery if critical
                if health_status['status'] == 'critical':
                    self.logger.warning("Display driver in critical state!")
                    
        except Exception as e:
            self.logger.error(f"Status reporting failed: {e}")
    
    def stop(self):
        """Stop the display driver monitoring service"""
        if not self.running:
            return
        
        print("🛑 Stopping Display Driver Service...")
        self.running = False
        
        try:
            if self.display_monitor:
                self.display_monitor.stop_monitoring()
                self.display_monitor = None
            
            if self.error_recovery:
                self.error_recovery.stop()
                self.error_recovery = None
            
            self.logger.info("Display driver service stopped")
            print("✅ Display Driver Service stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping service: {e}")
    
    def get_status(self):
        """Get current service status"""
        if not self.running or not self.display_monitor:
            return {"service_running": False}
        
        status = self.display_monitor.get_health_status()
        status["service_running"] = True
        return status


def main():
    """Main entry point"""
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "status":
            # Check service status
            try:
                # This would normally check if service is running via PID file
                print("Display Driver Service Status: Checking...")
                # For now, just run a quick health check
                from src.tsv6.hardware.display_driver_monitor import get_display_system_info, check_display_driver_warnings
                
                info = get_display_system_info()
                warning_count, warnings = check_display_driver_warnings()
                
                print(f"GPU Memory Split: {info['gpu_memory_split']}MB")
                print(f"Display Mode: {info['display_mode']}")
                print(f"Recent Warnings: {warning_count}")
                print(f"Driver Loaded: {info['driver_loaded']}")
                
                if warning_count > 0:
                    print("⚠️ Recent warnings detected:")
                    for warning in warnings[:3]:
                        print(f"  {warning[:80]}...")
                        
            except Exception as e:
                print(f"❌ Status check failed: {e}")
            return
            
        elif command == "test":
            # Test display driver monitor
            print("🧪 Testing Display Driver Monitor...")
            try:
                monitor = DisplayDriverMonitor()
                status = monitor.force_health_check()
                print(f"Test Result: {status}")
                
            except Exception as e:
                print(f"❌ Test failed: {e}")
            return
    
    # Default: Run as service
    service = DisplayDriverService()
    
    try:
        service.start()
    except KeyboardInterrupt:
        print("\n🛑 Service interrupted")
    finally:
        service.stop()


if __name__ == "__main__":
    main()
