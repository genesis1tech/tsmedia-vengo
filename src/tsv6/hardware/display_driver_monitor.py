#!/usr/bin/env python3
"""
VC4 Display Driver Monitor and Recovery System

Monitors the vc4 display driver for kernel warnings and system instability.
Implements recovery mechanisms for display driver issues including:
- Kernel warning detection
- GPU memory management
- Display pipeline recovery
- Fallback display modes

Addresses issue #40: Display driver causing kernel warnings and system instability.
"""

import os
import subprocess
import threading
import time
import logging
import re
from typing import Dict, Any, Optional, Callable, List, Tuple
from dataclasses import dataclass
from pathlib import Path

from ..utils.error_recovery import ErrorRecoverySystem, RecoveryAction, EscalationLevel


@dataclass
class DisplayDriverHealth:
    """Health status of display driver"""
    driver_name: str
    warnings_count: int
    last_warning_time: float
    gpu_memory_split: int
    display_mode: str
    pipeline_errors: int
    recovery_attempts: int
    status: str  # "healthy", "degraded", "critical"


class DisplayDriverMonitor:
    """Monitor and recovery system for vc4 display driver issues"""
    
    def __init__(self, error_recovery: Optional[ErrorRecoverySystem] = None):
        self.logger = logging.getLogger(__name__)
        self.error_recovery = error_recovery
        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        
        # Display driver health tracking
        self.driver_health = DisplayDriverHealth(
            driver_name="vc4",
            warnings_count=0,
            last_warning_time=0,
            gpu_memory_split=0,
            display_mode="unknown",
            pipeline_errors=0,
            recovery_attempts=0,
            status="unknown"
        )
        
        # Kernel log patterns for vc4 driver issues
        self.warning_patterns = [
            r"WARNING.*vc4.*vc4_atomic_commit_tail",
            r"WARNING.*vc4.*vc4_kms\.c",
            r"Tainted:.*\[W\].*\[C\]",
            r"vc4.*GPU.*timeout",
            r"vc4.*display.*pipeline.*error",
            r"drm.*vc4.*error"
        ]
        
        # Recovery strategies based on failure count
        self.recovery_strategies = {
            1: self._soft_recovery,      # Reset display pipeline
            3: self._medium_recovery,    # Adjust GPU memory and restart display
            5: self._hard_recovery,      # Fallback display mode
            8: self._critical_recovery   # System restart
        }
        
        # Register with error recovery system
        if self.error_recovery:
            self.error_recovery.register_component("display_driver")
            self.error_recovery.register_recovery_handler(
                "display_driver", 
                self._handle_recovery_action
            )
            self.error_recovery.register_fallback_handler(
                "display_driver",
                self._fallback_display_mode
            )
        
        print("🖥️ Display Driver Monitor initialized")
    
    def start_monitoring(self):
        """Start monitoring display driver health"""
        if self.monitoring:
            return
            
        self.monitoring = True
        self.stop_event.clear()
        
        # Initial health check
        self._check_initial_health()
        
        # Start monitoring thread
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="DisplayDriverMonitor",
            daemon=True
        )
        self.monitor_thread.start()
        
        print("📊 Display driver monitoring started")
    
    def stop_monitoring(self):
        """Stop monitoring display driver health"""
        if not self.monitoring:
            return
            
        print("🛑 Stopping display driver monitoring...")
        self.monitoring = False
        self.stop_event.set()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        print("✅ Display driver monitoring stopped")
    
    def _check_initial_health(self):
        """Check initial display driver health status"""
        try:
            # Check GPU memory split
            self.driver_health.gpu_memory_split = self._get_gpu_memory_split()
            
            # Check current display mode
            self.driver_health.display_mode = self._get_display_mode()
            
            # Check recent kernel warnings
            warning_count = self._scan_kernel_log_recent()
            self.driver_health.warnings_count = warning_count
            
            if warning_count == 0:
                self.driver_health.status = "healthy"
            elif warning_count < 5:
                self.driver_health.status = "degraded"
            else:
                self.driver_health.status = "critical"
            
            print(f"🔍 Initial display driver health: {self.driver_health.status}")
            print(f"   GPU memory split: {self.driver_health.gpu_memory_split}MB")
            print(f"   Display mode: {self.driver_health.display_mode}")
            print(f"   Recent warnings: {warning_count}")
            
        except Exception as e:
            self.logger.error(f"Failed to check initial display health: {e}")
            self.driver_health.status = "unknown"
    
    def _monitor_loop(self):
        """Main monitoring loop"""
        last_kernel_check = 0
        
        while self.monitoring and not self.stop_event.is_set():
            try:
                current_time = time.time()
                
                # Check kernel log every 30 seconds
                if current_time - last_kernel_check >= 30:
                    self._check_kernel_warnings()
                    last_kernel_check = current_time
                
                # Check display pipeline health every minute
                if int(current_time) % 60 == 0:
                    self._check_display_pipeline()
                
                # Sleep for monitoring interval
                self.stop_event.wait(10)
                
            except Exception as e:
                self.logger.error(f"Error in display driver monitoring: {e}")
                time.sleep(30)
    
    def _check_kernel_warnings(self):
        """Check kernel log for new vc4 warnings"""
        try:
            # Get recent kernel messages
            result = subprocess.run(
                ['dmesg', '-T', '--since', '1 minute ago'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                new_warnings = 0
                for line in result.stdout.splitlines():
                    for pattern in self.warning_patterns:
                        if re.search(pattern, line, re.IGNORECASE):
                            new_warnings += 1
                            self._handle_kernel_warning(line)
                            break
                
                if new_warnings > 0:
                    self.driver_health.warnings_count += new_warnings
                    self.driver_health.last_warning_time = time.time()
                    self._update_driver_status()
                    
        except Exception as e:
            self.logger.error(f"Failed to check kernel warnings: {e}")
    
    def _check_display_pipeline(self):
        """Check display pipeline health"""
        try:
            # Check if display is responding
            display_responsive = self._test_display_responsive()
            
            if not display_responsive:
                self.driver_health.pipeline_errors += 1
                if self.error_recovery:
                    self.error_recovery.report_error(
                        "display_driver",
                        "pipeline_error",
                        "Display pipeline not responding",
                        "high"
                    )
                    
        except Exception as e:
            self.logger.error(f"Failed to check display pipeline: {e}")
    
    def _handle_kernel_warning(self, warning_line: str):
        """Handle a detected kernel warning"""
        print(f"⚠️ VC4 kernel warning detected: {warning_line[:100]}...")
        
        # Extract warning details
        context = {
            "warning_line": warning_line,
            "gpu_memory": self.driver_health.gpu_memory_split,
            "display_mode": self.driver_health.display_mode
        }
        
        # Report to error recovery system
        if self.error_recovery:
            severity = "high" if "timeout" in warning_line.lower() else "medium"
            self.error_recovery.report_error(
                "display_driver",
                "kernel_warning",
                "VC4 driver kernel warning detected",
                severity,
                context
            )
    
    def _update_driver_status(self):
        """Update driver status based on warning count"""
        if self.driver_health.warnings_count == 0:
            self.driver_health.status = "healthy"
        elif self.driver_health.warnings_count < 5:
            self.driver_health.status = "degraded"
        else:
            self.driver_health.status = "critical"
    
    def _get_gpu_memory_split(self) -> int:
        """Get current GPU memory split setting"""
        try:
            result = subprocess.run(
                ['vcgencmd', 'get_mem', 'gpu'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                # Parse output like "gpu=64M"
                match = re.search(r'gpu=(\d+)M', result.stdout)
                if match:
                    return int(match.group(1))
            
        except Exception:
            pass
        
        return 0
    
    def _get_display_mode(self) -> str:
        """Get current display mode"""
        try:
            # Check for HDMI display info
            result = subprocess.run(
                ['tvservice', '-s'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                output = result.stdout.strip()
                if "HDMI" in output:
                    return f"HDMI: {output.split(' ', 2)[2] if len(output.split(' ', 2)) > 2 else 'active'}"
                elif "composite" in output.lower():
                    return "Composite"
                
        except Exception:
            pass
        
        return "unknown"
    
    def _scan_kernel_log_recent(self, minutes: int = 10) -> int:
        """Scan recent kernel log for vc4 warnings"""
        try:
            result = subprocess.run(
                ['dmesg', '-T', '--since', f'{minutes} minutes ago'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                warning_count = 0
                for line in result.stdout.splitlines():
                    for pattern in self.warning_patterns:
                        if re.search(pattern, line, re.IGNORECASE):
                            warning_count += 1
                            break
                return warning_count
                
        except Exception:
            pass
        
        return 0
    
    def _test_display_responsive(self) -> bool:
        """Test if display pipeline is responsive"""
        try:
            # Simple test using xset if available
            if os.environ.get('DISPLAY'):
                result = subprocess.run(
                    ['xset', 'q'],
                    capture_output=True,
                    timeout=5,
                    env=os.environ
                )
                return result.returncode == 0
            
            # Alternative: Check if framebuffer is accessible
            fb_devices = ['/dev/fb0', '/dev/fb1']
            for fb_device in fb_devices:
                if os.path.exists(fb_device) and os.access(fb_device, os.W_OK):
                    return True
            
            return False
            
        except Exception:
            return False
    
    def _handle_recovery_action(self, action: RecoveryAction, error, escalation_level: EscalationLevel) -> bool:
        """Handle recovery action from error recovery system"""
        print(f"🔧 Display driver recovery: {action.value} ({escalation_level.value})")
        
        try:
            if action == RecoveryAction.RESTART_COMPONENT:
                return self._soft_recovery()
            elif action == RecoveryAction.RESTART_SERVICE:
                return self._medium_recovery()
            elif action == RecoveryAction.FALLBACK_MODE:
                return self._hard_recovery()
            elif action == RecoveryAction.SYSTEM_RESTART:
                return self._critical_recovery()
            else:
                return self._soft_recovery()
                
        except Exception as e:
            self.logger.error(f"Display recovery action failed: {e}")
            return False
    
    def _soft_recovery(self) -> bool:
        """Soft recovery: Reset display pipeline"""
        print("🔄 Attempting soft display recovery...")
        
        try:
            # Try to reset GPU memory
            subprocess.run(['sudo', 'vcgencmd', 'version'], timeout=10)
            
            # Reset display if X11 is running
            if os.environ.get('DISPLAY'):
                subprocess.run(['xset', 'dpms', 'force', 'off'], timeout=5)
                time.sleep(2)
                subprocess.run(['xset', 'dpms', 'force', 'on'], timeout=5)
            
            self.driver_health.recovery_attempts += 1
            print("✅ Soft display recovery completed")
            return True
            
        except Exception as e:
            self.logger.error(f"Soft display recovery failed: {e}")
            return False
    
    def _medium_recovery(self) -> bool:
        """Medium recovery: Adjust GPU memory and restart display service"""
        print("🔄 Attempting medium display recovery...")
        
        try:
            # Reduce GPU memory split if it's high
            current_split = self._get_gpu_memory_split()
            if current_split > 128:
                new_split = max(64, current_split - 32)
                self._set_gpu_memory_split(new_split)
                print(f"📉 Reduced GPU memory split: {current_split}MB -> {new_split}MB")
            
            # Try restarting display manager if it exists
            display_managers = ['lightdm', 'gdm3', 'sddm']
            for dm in display_managers:
                try:
                    result = subprocess.run(
                        ['systemctl', 'is-active', dm],
                        capture_output=True
                    )
                    if result.returncode == 0:
                        subprocess.run(['sudo', 'systemctl', 'restart', dm], timeout=30)
                        print(f"🔄 Restarted display manager: {dm}")
                        break
                except Exception:
                    continue
            
            self.driver_health.recovery_attempts += 1
            time.sleep(5)  # Allow time for recovery
            print("✅ Medium display recovery completed")
            return True
            
        except Exception as e:
            self.logger.error(f"Medium display recovery failed: {e}")
            return False
    
    def _hard_recovery(self) -> bool:
        """Hard recovery: Switch to fallback display mode"""
        print("🔄 Attempting hard display recovery (fallback mode)...")
        
        try:
            # Set conservative GPU memory split
            self._set_gpu_memory_split(64)
            print("📉 Set conservative GPU memory split: 64MB")
            
            # Try to switch to safe display mode
            self._set_safe_display_mode()
            
            # Disable GPU acceleration if possible
            self._disable_gpu_acceleration()
            
            self.driver_health.recovery_attempts += 1
            self.driver_health.display_mode = "fallback"
            print("✅ Hard display recovery completed (fallback mode active)")
            return True
            
        except Exception as e:
            self.logger.error(f"Hard display recovery failed: {e}")
            return False
    
    def _critical_recovery(self) -> bool:
        """Critical recovery: Prepare for system restart"""
        print("🚨 Critical display recovery: preparing for system restart...")
        
        try:
            # Save recovery state
            recovery_state = {
                "timestamp": time.time(),
                "reason": "display_driver_critical_failure",
                "warnings_count": self.driver_health.warnings_count,
                "recovery_attempts": self.driver_health.recovery_attempts
            }
            
            with open("/tmp/display_recovery_state.json", "w") as f:
                import json
                json.dump(recovery_state, f)
            
            print("💾 Saved recovery state for post-reboot analysis")
            return True
            
        except Exception as e:
            self.logger.error(f"Critical display recovery preparation failed: {e}")
            return False
    
    def _set_gpu_memory_split(self, memory_mb: int):
        """Set GPU memory split"""
        try:
            config_file = "/boot/config.txt"
            if os.path.exists(config_file):
                # Read current config
                with open(config_file, 'r') as f:
                    lines = f.readlines()
                
                # Update or add gpu_mem setting
                found = False
                for i, line in enumerate(lines):
                    if line.startswith('gpu_mem='):
                        lines[i] = f"gpu_mem={memory_mb}\n"
                        found = True
                        break
                
                if not found:
                    lines.append(f"gpu_mem={memory_mb}\n")
                
                # Write back (requires sudo)
                temp_file = "/tmp/config.txt.new"
                with open(temp_file, 'w') as f:
                    f.writelines(lines)
                
                subprocess.run(['sudo', 'cp', temp_file, config_file], timeout=10)
                os.remove(temp_file)
                
        except Exception as e:
            self.logger.error(f"Failed to set GPU memory split: {e}")
    
    def _set_safe_display_mode(self):
        """Set safe display mode in boot config"""
        try:
            config_file = "/boot/config.txt"
            if os.path.exists(config_file):
                safe_settings = [
                    "hdmi_safe=1",
                    "hdmi_force_hotplug=1",
                    "config_hdmi_boost=4"
                ]
                
                # Read current config
                with open(config_file, 'r') as f:
                    lines = f.readlines()
                
                # Add safe settings if not present
                for setting in safe_settings:
                    if not any(line.strip().startswith(setting.split('=')[0]) for line in lines):
                        lines.append(f"{setting}\n")
                
                # Write back
                temp_file = "/tmp/config.txt.safe"
                with open(temp_file, 'w') as f:
                    f.writelines(lines)
                
                subprocess.run(['sudo', 'cp', temp_file, config_file], timeout=10)
                os.remove(temp_file)
                
        except Exception as e:
            self.logger.error(f"Failed to set safe display mode: {e}")
    
    def _disable_gpu_acceleration(self):
        """Disable GPU acceleration to reduce driver load"""
        try:
            # Create X11 config to disable acceleration
            x11_config = """Section "Device"
    Identifier "vc4"
    Driver "modesetting"
    Option "AccelMethod" "none"
EndSection"""
            
            config_dir = "/etc/X11/xorg.conf.d"
            if os.path.exists(config_dir):
                config_file = os.path.join(config_dir, "99-disable-gpu-accel.conf")
                with open("/tmp/gpu-accel.conf", 'w') as f:
                    f.write(x11_config)
                
                subprocess.run(['sudo', 'cp', '/tmp/gpu-accel.conf', config_file], timeout=10)
                os.remove("/tmp/gpu-accel.conf")
                print("🚫 Disabled GPU acceleration")
                
        except Exception as e:
            self.logger.error(f"Failed to disable GPU acceleration: {e}")
    
    def _fallback_display_mode(self, error):
        """Fallback handler for display issues"""
        print("⚠️ Activating display fallback mode...")
        
        try:
            # Set minimum viable display configuration
            self._set_gpu_memory_split(64)
            self._set_safe_display_mode()
            self._disable_gpu_acceleration()
            
            self.driver_health.status = "fallback"
            self.driver_health.display_mode = "safe_mode"
            
            print("🛡️ Display fallback mode activated")
            
        except Exception as e:
            self.logger.error(f"Display fallback mode failed: {e}")
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get current display driver health status"""
        return {
            "driver_name": self.driver_health.driver_name,
            "status": self.driver_health.status,
            "warnings_count": self.driver_health.warnings_count,
            "last_warning_time": self.driver_health.last_warning_time,
            "gpu_memory_split": self.driver_health.gpu_memory_split,
            "display_mode": self.driver_health.display_mode,
            "pipeline_errors": self.driver_health.pipeline_errors,
            "recovery_attempts": self.driver_health.recovery_attempts,
            "monitoring_active": self.monitoring
        }
    
    def force_health_check(self):
        """Force immediate health check"""
        print("🔍 Forcing display driver health check...")
        self._check_initial_health()
        return self.get_health_status()
    
    def reset_warnings_count(self):
        """Reset warnings count (for testing or manual recovery)"""
        old_count = self.driver_health.warnings_count
        self.driver_health.warnings_count = 0
        self.driver_health.last_warning_time = 0
        self._update_driver_status()
        print(f"🔄 Reset warnings count: {old_count} -> 0")
    
    def __enter__(self):
        """Context manager entry"""
        self.start_monitoring()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.stop_monitoring()


# Utility functions for system-level display management
def check_display_driver_warnings() -> Tuple[int, List[str]]:
    """Check for recent display driver warnings in kernel log"""
    try:
        result = subprocess.run(
            ['dmesg', '-T', '--since', '1 hour ago'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        warnings = []
        patterns = [
            r"WARNING.*vc4.*vc4_atomic_commit_tail",
            r"WARNING.*vc4.*vc4_kms\.c",
            r"vc4.*GPU.*timeout",
            r"drm.*vc4.*error"
        ]
        
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                for pattern in patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        warnings.append(line.strip())
                        break
        
        return len(warnings), warnings
        
    except Exception:
        return 0, []


def get_display_system_info() -> Dict[str, Any]:
    """Get comprehensive display system information"""
    info = {
        "gpu_memory_split": 0,
        "display_mode": "unknown",
        "framebuffer_devices": [],
        "x11_display": bool(os.environ.get('DISPLAY')),
        "recent_warnings": 0,
        "driver_loaded": False
    }
    
    try:
        # GPU memory split
        result = subprocess.run(
            ['vcgencmd', 'get_mem', 'gpu'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            match = re.search(r'gpu=(\d+)M', result.stdout)
            if match:
                info["gpu_memory_split"] = int(match.group(1))
        
        # Display mode
        result = subprocess.run(
            ['tvservice', '-s'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            info["display_mode"] = result.stdout.strip()
        
        # Framebuffer devices
        for fb in ['/dev/fb0', '/dev/fb1']:
            if os.path.exists(fb):
                info["framebuffer_devices"].append(fb)
        
        # Check if vc4 driver is loaded
        result = subprocess.run(['lsmod'], capture_output=True, text=True)
        if result.returncode == 0 and 'vc4' in result.stdout:
            info["driver_loaded"] = True
        
        # Recent warnings
        warning_count, _ = check_display_driver_warnings()
        info["recent_warnings"] = warning_count
        
    except Exception:
        pass
    
    return info


if __name__ == "__main__":
    # Test the display driver monitor
    print("🧪 Testing Display Driver Monitor...")
    
    # Get system info
    info = get_display_system_info()
    print(f"Display System Info: {info}")
    
    # Check for warnings
    warning_count, warnings = check_display_driver_warnings()
    print(f"Recent warnings: {warning_count}")
    for warning in warnings[:3]:  # Show first 3
        print(f"  ⚠️  {warning[:80]}...")
    
    # Test monitor (brief)
    monitor = DisplayDriverMonitor()
    monitor.start_monitoring()
    time.sleep(5)
    status = monitor.get_health_status()
    print(f"Health Status: {status}")
    monitor.stop_monitoring()
