#!/usr/bin/env python3
"""
Memory Management Utilities for TSV6 Raspberry Pi Systems
Provides emergency memory cleanup, process management, and resource optimization
"""

import os
import sys
import time
import signal
import psutil
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple


class MemoryUtils:
    """Memory management utility functions"""
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        
        # Memory thresholds for emergency actions (percentage)
        self.emergency_memory_threshold = 95
        self.emergency_swap_threshold = 90
        
        # Process management settings
        self.protected_processes = ['init', 'kthreadd', 'systemd', 'ssh', 'sshd']
        self.critical_services = ['NetworkManager', 'systemd-', 'dbus']
    
    def get_memory_hogs(self, threshold_mb: int = 50, limit: int = 10) -> List[Dict]:
        """Find processes consuming excessive memory"""
        memory_hogs = []
        
        for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'memory_percent', 'cmdline']):
            try:
                proc_info = proc.info
                memory_mb = proc_info['memory_info'].rss / (1024 * 1024)
                
                if memory_mb > threshold_mb:
                    cmdline = ' '.join(proc_info['cmdline'][:3]) if proc_info['cmdline'] else proc_info['name']
                    memory_hogs.append({
                        'pid': proc_info['pid'],
                        'name': proc_info['name'],
                        'cmdline': cmdline,
                        'memory_mb': round(memory_mb, 1),
                        'memory_percent': round(proc_info['memory_percent'], 1)
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
        return sorted(memory_hogs, key=lambda x: x['memory_mb'], reverse=True)[:limit]
    
    def is_process_safe_to_kill(self, proc: psutil.Process) -> bool:
        """Check if a process is safe to terminate"""
        try:
            # Don't kill critical system processes
            if proc.name() in self.protected_processes:
                return False
            
            # Don't kill processes owned by root that are likely system critical
            if proc.username() == 'root':
                name = proc.name()
                for critical in self.critical_services:
                    if critical in name:
                        return False
            
            # Don't kill kernel threads
            if proc.ppid() == 2:  # kthreadd
                return False
                
            # Don't kill the current process or its parent
            current_pid = os.getpid()
            if proc.pid == current_pid or proc.pid == os.getppid():
                return False
                
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    
    def emergency_kill_memory_hogs(self, min_memory_mb: int = 100, max_kills: int = 3) -> List[Dict]:
        """Emergency termination of high-memory processes"""
        killed_processes = []
        
        self.logger.warning("Starting emergency memory cleanup - killing memory hogs")
        
        # Get memory hogs above threshold
        memory_hogs = self.get_memory_hogs(min_memory_mb, max_kills * 2)
        
        killed_count = 0
        for hog in memory_hogs:
            if killed_count >= max_kills:
                break
                
            try:
                proc = psutil.Process(hog['pid'])
                if self.is_process_safe_to_kill(proc):
                    self.logger.warning(f"Emergency killing process: {hog['name']} (PID: {hog['pid']}, {hog['memory_mb']}MB)")
                    
                    # Try graceful termination first
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        # Force kill if graceful termination failed
                        self.logger.warning(f"Force killing process: {hog['name']} (PID: {hog['pid']})")
                        proc.kill()
                        proc.wait(timeout=2)
                    
                    killed_processes.append(hog)
                    killed_count += 1
                    
                    # Wait a moment for memory to be freed
                    time.sleep(1)
                else:
                    self.logger.info(f"Skipping protected process: {hog['name']} (PID: {hog['pid']})")
                    
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                self.logger.warning(f"Failed to kill process: {hog['name']} (PID: {hog['pid']})")
                continue
        
        return killed_processes
    
    def clear_system_caches(self) -> Dict[str, str]:
        """Clear various system caches to free memory"""
        results = {}
        
        try:
            # Sync filesystem
            subprocess.run(['sync'], check=True, timeout=10)
            results['sync'] = 'success'
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            results['sync'] = 'failed'
        
        # Drop caches (requires root)
        try:
            if os.geteuid() == 0:
                # Drop page cache
                with open('/proc/sys/vm/drop_caches', 'w') as f:
                    f.write('1')
                time.sleep(1)
                
                # Drop dentries and inodes
                with open('/proc/sys/vm/drop_caches', 'w') as f:
                    f.write('2')
                time.sleep(1)
                
                # Drop all caches
                with open('/proc/sys/vm/drop_caches', 'w') as f:
                    f.write('3')
                
                results['drop_caches'] = 'success'
                self.logger.info("Successfully cleared system caches")
            else:
                results['drop_caches'] = 'no_root_access'
        except Exception as e:
            results['drop_caches'] = f'failed: {e}'
        
        # Clear temporary files
        try:
            temp_dirs = ['/tmp', '/var/tmp']
            for temp_dir in temp_dirs:
                if os.path.exists(temp_dir):
                    # Remove files older than 1 hour in temp directories
                    subprocess.run(['find', temp_dir, '-type', 'f', '-mmin', '+60', '-delete'], 
                                 check=False, timeout=30)
            results['temp_cleanup'] = 'success'
        except Exception as e:
            results['temp_cleanup'] = f'failed: {e}'
        
        return results
    
    def optimize_oom_killer(self) -> Dict[str, str]:
        """Configure OOM killer to be more aggressive with non-critical processes"""
        results = {}
        
        try:
            # Make our main application process less likely to be killed
            current_pid = os.getpid()
            oom_score_file = f'/proc/{current_pid}/oom_score_adj'
            
            if os.path.exists(oom_score_file) and os.geteuid() == 0:
                with open(oom_score_file, 'w') as f:
                    f.write('-100')  # Less likely to be killed
                results['oom_protection'] = 'success'
            else:
                results['oom_protection'] = 'no_root_access'
                
        except Exception as e:
            results['oom_protection'] = f'failed: {e}'
        
        return results
    
    def emergency_memory_recovery(self) -> Dict:
        """Perform comprehensive emergency memory recovery"""
        self.logger.critical("Starting emergency memory recovery procedures")
        
        recovery_results = {
            'timestamp': time.time(),
            'initial_memory': self._get_memory_summary(),
            'actions_performed': [],
            'processes_killed': [],
            'final_memory': None,
            'success': False
        }
        
        try:
            # Step 1: Clear caches
            self.logger.info("Step 1: Clearing system caches")
            cache_results = self.clear_system_caches()
            recovery_results['cache_cleanup'] = cache_results
            recovery_results['actions_performed'].append('cache_cleanup')
            
            # Step 2: Kill memory hogs if still critical
            memory_after_cache = psutil.virtual_memory().percent
            if memory_after_cache > self.emergency_memory_threshold:
                self.logger.warning("Step 2: Memory still critical, killing memory hogs")
                killed_procs = self.emergency_kill_memory_hogs()
                recovery_results['processes_killed'] = killed_procs
                recovery_results['actions_performed'].append('kill_memory_hogs')
            
            # Step 3: Optimize OOM killer
            oom_results = self.optimize_oom_killer()
            recovery_results['oom_optimization'] = oom_results
            recovery_results['actions_performed'].append('oom_optimization')
            
            # Final memory check
            recovery_results['final_memory'] = self._get_memory_summary()
            
            final_memory_percent = recovery_results['final_memory']['memory_percent']
            if final_memory_percent < self.emergency_memory_threshold:
                recovery_results['success'] = True
                self.logger.info(f"Emergency recovery successful: Memory usage reduced to {final_memory_percent:.1f}%")
            else:
                self.logger.error(f"Emergency recovery failed: Memory usage still at {final_memory_percent:.1f}%")
            
        except Exception as e:
            self.logger.error(f"Emergency recovery failed with exception: {e}")
            recovery_results['error'] = str(e)
        
        return recovery_results
    
    def _get_memory_summary(self) -> Dict:
        """Get current memory summary"""
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        return {
            'memory_percent': memory.percent,
            'memory_available_mb': round(memory.available / (1024*1024)),
            'swap_percent': swap.percent,
            'swap_used_mb': round(swap.used / (1024*1024))
        }
    
    def monitor_memory_pressure(self, callback_func=None, check_interval: int = 10):
        """Monitor memory pressure and trigger emergency actions"""
        self.logger.info(f"Starting memory pressure monitoring (interval: {check_interval}s)")
        
        while True:
            try:
                memory = psutil.virtual_memory()
                swap = psutil.swap_memory()
                
                # Check for emergency conditions
                if (memory.percent > self.emergency_memory_threshold or 
                    swap.percent > self.emergency_swap_threshold):
                    
                    self.logger.critical(f"EMERGENCY: Memory {memory.percent:.1f}%, Swap {swap.percent:.1f}%")
                    
                    # Perform emergency recovery
                    recovery_results = self.emergency_memory_recovery()
                    
                    # Call callback if provided
                    if callback_func:
                        callback_func('emergency', recovery_results)
                
                time.sleep(check_interval)
                
            except KeyboardInterrupt:
                self.logger.info("Memory pressure monitoring stopped")
                break
            except Exception as e:
                self.logger.error(f"Error in memory pressure monitoring: {e}")
                time.sleep(check_interval)


def main():
    """Main CLI interface for memory utilities"""
    import argparse
    
    logging.basicConfig(level=logging.INFO, 
                       format='%(asctime)s - %(levelname)s - %(message)s')
    
    parser = argparse.ArgumentParser(description='TSV6 Memory Management Utilities')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Memory hogs command
    hogs_parser = subparsers.add_parser('hogs', help='Find memory-consuming processes')
    hogs_parser.add_argument('--threshold', type=int, default=50, 
                           help='Memory threshold in MB (default: 50)')
    hogs_parser.add_argument('--limit', type=int, default=10,
                           help='Max number of processes to show (default: 10)')
    
    # Emergency cleanup command
    cleanup_parser = subparsers.add_parser('emergency', help='Perform emergency memory cleanup')
    cleanup_parser.add_argument('--kill-threshold', type=int, default=100,
                              help='Kill processes using more than X MB (default: 100)')
    cleanup_parser.add_argument('--max-kills', type=int, default=3,
                              help='Maximum processes to kill (default: 3)')
    
    # Cache cleanup command
    subparsers.add_parser('clear-cache', help='Clear system caches')
    
    # Monitor command
    monitor_parser = subparsers.add_parser('monitor', help='Monitor memory pressure')
    monitor_parser.add_argument('--interval', type=int, default=10,
                              help='Check interval in seconds (default: 10)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    utils = MemoryUtils()
    
    if args.command == 'hogs':
        hogs = utils.get_memory_hogs(args.threshold, args.limit)
        print(f"Memory-consuming processes (>{args.threshold}MB):")
        print(f"{'PID':<8} {'Name':<20} {'Memory (MB)':<12} {'Memory %':<10} {'Command'}")
        print("-" * 70)
        for hog in hogs:
            print(f"{hog['pid']:<8} {hog['name']:<20} {hog['memory_mb']:<12} {hog['memory_percent']:<10} {hog['cmdline'][:30]}")
    
    elif args.command == 'emergency':
        print("Performing emergency memory cleanup...")
        results = utils.emergency_memory_recovery()
        print(f"Emergency cleanup completed: {'SUCCESS' if results['success'] else 'FAILED'}")
        print(f"Actions performed: {', '.join(results['actions_performed'])}")
        if results['processes_killed']:
            print(f"Processes killed: {len(results['processes_killed'])}")
            for proc in results['processes_killed']:
                print(f"  - {proc['name']} (PID: {proc['pid']}, {proc['memory_mb']}MB)")
    
    elif args.command == 'clear-cache':
        print("Clearing system caches...")
        results = utils.clear_system_caches()
        for action, result in results.items():
            print(f"{action}: {result}")
    
    elif args.command == 'monitor':
        print(f"Starting memory pressure monitoring (Ctrl+C to stop)...")
        utils.monitor_memory_pressure(check_interval=args.interval)


if __name__ == '__main__':
    main()