#!/usr/bin/env python3
"""
Watchdog Monitoring System for TSV6
Detects unexpected restarts and watchdog events, publishes to AWS IoT
"""

import subprocess
import json
import time
import os
from pathlib import Path
from datetime import datetime

class WatchdogMonitor:
    """Monitors system watchdog events and unexpected restarts"""
    
    def __init__(self):
        """Initialize watchdog monitor"""
        self.boot_id_file = Path.home() / ".cache" / "tsv6_boot_id"
        self.boot_id_file.parent.mkdir(parents=True, exist_ok=True)
        self.current_boot_id = self._get_current_boot_id()
        self.previous_boot_id = self._get_previous_boot_id()
        self.unexpected_restart = self._detect_unexpected_restart()
        
        print(f"🔍 Watchdog Monitor initialized")
        print(f"   Current boot ID: {self.current_boot_id[:8]}...")
        print(f"   Previous boot ID: {self.previous_boot_id[:8] if self.previous_boot_id else 'None'}...")
        if self.unexpected_restart:
            print(f"   ⚠️  Unexpected restart detected!")
    
    def _get_current_boot_id(self) -> str:
        """Get current system boot ID from journalctl"""
        try:
            result = subprocess.run(
                ["journalctl", "--boot", "-n", "1", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout:
                lines = result.stdout.strip().split('\n')
                if lines:
                    data = json.loads(lines[0])
                    return data.get("_BOOT_ID", "unknown")
        except Exception as e:
            print(f"⚠️  Error getting boot ID: {e}")
        return "unknown"
    
    def _get_previous_boot_id(self) -> str:
        """Get boot ID from previous session"""
        try:
            if self.boot_id_file.exists():
                return self.boot_id_file.read_text().strip()
        except Exception as e:
            print(f"⚠️  Error reading previous boot ID: {e}")
        return None
    
    def _detect_unexpected_restart(self) -> bool:
        """Detect if device had an unexpected restart"""
        if not self.previous_boot_id or not self.current_boot_id:
            return False
        return self.previous_boot_id != self.current_boot_id
    
    def save_boot_id(self):
        """Save current boot ID for next session"""
        try:
            self.boot_id_file.write_text(self.current_boot_id)
        except Exception as e:
            print(f"⚠️  Error saving boot ID: {e}")
    
    def get_restart_info(self) -> dict:
        """Get detailed information about unexpected restart"""
        info = {
            "unexpected_restart": self.unexpected_restart,
            "current_boot_id": self.current_boot_id,
            "timestamp": int(time.time()),
            "iso_timestamp": datetime.now().isoformat(),
            "restart_reason": "unknown",
            "watchdog_events": [],
            "kernel_panics": []
        }
        
        if self.unexpected_restart:
            info["restart_reason"] = self._determine_restart_reason()
            info["watchdog_events"] = self._get_watchdog_events()
            info["kernel_panics"] = self._get_kernel_panics()
        
        return info
    
    def _determine_restart_reason(self) -> str:
        """Determine what caused the restart"""
        try:
            # Check for watchdog timeout
            result = subprocess.run(
                ["journalctl", "--boot=-1", "-n", "500", "-a"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            output = result.stdout.lower()
            
            if "watchdog" in output and "timeout" in output:
                return "watchdog_timeout"
            elif "kernel panic" in output:
                return "kernel_panic"
            elif "out of memory" in output:
                return "out_of_memory"
            elif "thermal" in output:
                return "thermal_shutdown"
            elif "power" in output:
                return "power_failure"
            else:
                return "unknown_restart"
                
        except Exception as e:
            print(f"⚠️  Error determining restart reason: {e}")
            return "unable_to_determine"
    
    def _get_watchdog_events(self) -> list:
        """Extract watchdog events from previous boot"""
        events = []
        try:
            result = subprocess.run(
                ["journalctl", "--boot=-1", "-g", "watchdog"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                for line in result.stdout.split('\n')[:10]:  # Get last 10 events
                    if line.strip():
                        events.append(line.strip())
        except Exception as e:
            print(f"⚠️  Error getting watchdog events: {e}")
        
        return events
    
    def _get_kernel_panics(self) -> list:
        """Extract kernel panic messages from previous boot"""
        panics = []
        try:
            result = subprocess.run(
                ["journalctl", "--boot=-1", "-g", "panic|oops|bug"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                for line in result.stdout.split('\n')[:10]:  # Get last 10 events
                    if line.strip():
                        panics.append(line.strip())
        except Exception as e:
            print(f"⚠️  Error getting kernel panics: {e}")
        
        return panics
    
    def get_current_boot_uptime(self) -> int:
        """Get uptime of previous boot before restart (in seconds)"""
        try:
            result = subprocess.run(
                ["journalctl", "--boot=-1", "-n", "1", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    data = json.loads(lines[0])
                    realtime_usec = int(data.get("__REALTIME_TIMESTAMP", 0))
                    # Get first entry timestamp
                    result2 = subprocess.run(
                        ["journalctl", "--boot=-1", "-n", "1000", "-o", "json"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result2.returncode == 0:
                        lines2 = result2.stdout.strip().split('\n')
                        if lines2:
                            first_data = json.loads(lines2[-1])
                            first_time = int(first_data.get("__REALTIME_TIMESTAMP", 0))
                            if realtime_usec and first_time:
                                return int((realtime_usec - first_time) / 1_000_000)
        except Exception as e:
            print(f"⚠️  Error calculating boot uptime: {e}")
        
        return 0


def main():
    """Test the watchdog monitor"""
    monitor = WatchdogMonitor()
    
    print("\n" + "=" * 70)
    print("WATCHDOG MONITOR TEST")
    print("=" * 70)
    
    restart_info = monitor.get_restart_info()
    print(f"\nRestart Information:")
    print(json.dumps(restart_info, indent=2))
    
    if monitor.unexpected_restart:
        print(f"\n⚠️  Device had unexpected restart!")
        print(f"Reason: {restart_info['restart_reason']}")
        print(f"Watchdog Events: {len(restart_info['watchdog_events'])}")
        print(f"Kernel Panics: {len(restart_info['kernel_panics'])}")
    else:
        print(f"\n✅ Normal boot (no unexpected restart detected)")
    
    # Save boot ID for next session
    monitor.save_boot_id()
    print(f"✅ Boot ID saved for next session")


if __name__ == "__main__":
    main()
