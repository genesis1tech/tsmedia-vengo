#!/usr/bin/env python3
"""
Memory Monitor for TSV6 Raspberry Pi Systems
Monitors memory usage, swap activity, and implements memory optimization strategies
"""

import os
import sys
import time
import json
import psutil
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Tuple

class MemoryMonitor:
    """Advanced memory monitoring and management system"""
    
    def __init__(self, config_file: Optional[str] = None):
        self.config = self._load_config(config_file)
        self.log_dir = Path(self.config.get('log_dir', '/tmp/memory_logs'))
        self.log_dir.mkdir(exist_ok=True, parents=True)
        
        # Setup logging
        log_file = self.log_dir / 'memory_monitor.log'
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Memory thresholds (configurable)
        self.memory_warning_threshold = self.config.get('memory_warning_threshold', 85)  # %
        self.memory_critical_threshold = self.config.get('memory_critical_threshold', 95)  # %
        self.swap_warning_threshold = self.config.get('swap_warning_threshold', 50)  # %
        self.swap_critical_threshold = self.config.get('swap_critical_threshold', 80)  # %
        
        # Action flags
        self.last_cleanup_time = 0
        self.cleanup_interval = self.config.get('cleanup_interval', 300)  # seconds
        
    def _load_config(self, config_file: Optional[str]) -> Dict:
        """Load configuration from file or use defaults"""
        default_config = {
            'log_dir': '/tmp/memory_logs',
            'memory_warning_threshold': 85,
            'memory_critical_threshold': 95,
            'swap_warning_threshold': 50,
            'swap_critical_threshold': 80,
            'cleanup_interval': 300,
            'enable_auto_cleanup': True,
            'enable_swap_optimization': True
        }
        
        if config_file and Path(config_file).exists():
            try:
                with open(config_file, 'r') as f:
                    user_config = json.load(f)
                default_config.update(user_config)
            except Exception as e:
                print(f"Warning: Could not load config file {config_file}: {e}")
        
        return default_config
    
    def get_memory_stats(self) -> Dict:
        """Get comprehensive memory statistics"""
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        # Get additional VM stats
        try:
            with open('/proc/vmstat', 'r') as f:
                vmstat = {}
                for line in f:
                    key, value = line.strip().split()
                    vmstat[key] = int(value)
        except Exception:
            vmstat = {}
        
        stats = {
            'timestamp': datetime.now().isoformat(),
            'memory': {
                'total': memory.total,
                'available': memory.available,
                'used': memory.used,
                'percent': memory.percent,
                'free': memory.free,
                'buffers': memory.buffers,
                'cached': memory.cached,
                'shared': memory.shared
            },
            'swap': {
                'total': swap.total,
                'used': swap.used,
                'free': swap.free,
                'percent': swap.percent
            },
            'vm_stats': {
                'pswpin': vmstat.get('pswpin', 0),
                'pswpout': vmstat.get('pswpout', 0),
                'pgfault': vmstat.get('pgfault', 0),
                'pgmajfault': vmstat.get('pgmajfault', 0)
            }
        }
        
        return stats
    
    def get_top_memory_processes(self, limit: int = 10) -> List[Dict]:
        """Get top memory consuming processes"""
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'memory_percent', 'memory_info']):
            try:
                proc_info = proc.info
                if proc_info['memory_percent'] > 0:
                    processes.append({
                        'pid': proc_info['pid'],
                        'name': proc_info['name'],
                        'memory_percent': round(proc_info['memory_percent'], 2),
                        'memory_mb': round(proc_info['memory_info'].rss / (1024*1024), 1)
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        return sorted(processes, key=lambda x: x['memory_percent'], reverse=True)[:limit]
    
    def check_memory_health(self, stats: Dict) -> Tuple[str, str, List[str]]:
        """Analyze memory health and return status, severity, and recommendations"""
        memory_percent = stats['memory']['percent']
        swap_percent = stats['swap']['percent']
        
        issues = []
        recommendations = []
        
        # Memory analysis
        if memory_percent >= self.memory_critical_threshold:
            severity = 'CRITICAL'
            issues.append(f"Memory usage critical: {memory_percent:.1f}%")
            recommendations.extend([
                "Immediately free memory or restart high-memory processes",
                "Consider increasing system RAM",
                "Enable aggressive memory cleanup"
            ])
        elif memory_percent >= self.memory_warning_threshold:
            severity = 'WARNING'
            issues.append(f"Memory usage high: {memory_percent:.1f}%")
            recommendations.extend([
                "Monitor memory usage closely",
                "Consider cleaning system caches",
                "Review running processes for optimization"
            ])
        else:
            severity = 'OK'
        
        # Swap analysis
        if swap_percent >= self.swap_critical_threshold:
            severity = 'CRITICAL' if severity != 'CRITICAL' else severity
            issues.append(f"Swap usage critical: {swap_percent:.1f}%")
            recommendations.extend([
                "Immediately reduce memory pressure",
                "Increase swap file size",
                "Optimize swappiness settings"
            ])
        elif swap_percent >= self.swap_warning_threshold:
            if severity == 'OK':
                severity = 'WARNING'
            issues.append(f"Swap usage elevated: {swap_percent:.1f}%")
            recommendations.extend([
                "Monitor swap usage trends",
                "Consider memory optimization",
                "Check for memory leaks in applications"
            ])
        
        status = '; '.join(issues) if issues else f"Memory: {memory_percent:.1f}%, Swap: {swap_percent:.1f}%"
        
        return status, severity, recommendations
    
    def perform_memory_cleanup(self) -> Dict:
        """Perform system memory cleanup operations"""
        current_time = time.time()
        if current_time - self.last_cleanup_time < self.cleanup_interval:
            return {'skipped': True, 'reason': 'cleanup_interval_not_reached'}
        
        self.logger.info("Starting memory cleanup operations...")
        cleanup_results = {}
        
        try:
            # Drop caches (requires root)
            if os.geteuid() == 0:
                # Drop page cache, dentries and inodes
                subprocess.run(['sync'], check=True)
                with open('/proc/sys/vm/drop_caches', 'w') as f:
                    f.write('3')
                cleanup_results['drop_caches'] = 'success'
                self.logger.info("Dropped system caches")
            else:
                cleanup_results['drop_caches'] = 'skipped_no_root'
            
            # Compact memory if available
            try:
                with open('/proc/sys/vm/compact_memory', 'w') as f:
                    f.write('1')
                cleanup_results['compact_memory'] = 'success'
                self.logger.info("Triggered memory compaction")
            except Exception:
                cleanup_results['compact_memory'] = 'not_available'
            
            self.last_cleanup_time = current_time
            cleanup_results['timestamp'] = datetime.now().isoformat()
            
        except Exception as e:
            self.logger.error(f"Memory cleanup failed: {e}")
            cleanup_results['error'] = str(e)
        
        return cleanup_results
    
    def optimize_swap_settings(self) -> Dict:
        """Optimize swap settings based on system state"""
        if not self.config.get('enable_swap_optimization', True):
            return {'skipped': True, 'reason': 'swap_optimization_disabled'}
        
        results = {}
        
        try:
            # Read current swappiness
            with open('/proc/sys/vm/swappiness', 'r') as f:
                current_swappiness = int(f.read().strip())
            
            # Adjust swappiness based on memory pressure
            stats = self.get_memory_stats()
            memory_percent = stats['memory']['percent']
            
            # More aggressive swapping when memory is tight
            if memory_percent > 90:
                target_swappiness = 80
            elif memory_percent > 80:
                target_swappiness = 60
            else:
                target_swappiness = 40  # Less aggressive by default
            
            if current_swappiness != target_swappiness and os.geteuid() == 0:
                with open('/proc/sys/vm/swappiness', 'w') as f:
                    f.write(str(target_swappiness))
                results['swappiness_changed'] = {
                    'from': current_swappiness,
                    'to': target_swappiness
                }
                self.logger.info(f"Adjusted swappiness from {current_swappiness} to {target_swappiness}")
            else:
                results['swappiness'] = current_swappiness
            
        except Exception as e:
            results['error'] = str(e)
            self.logger.error(f"Swap optimization failed: {e}")
        
        return results
    
    def generate_report(self, stats: Dict) -> Dict:
        """Generate comprehensive memory report"""
        status, severity, recommendations = self.check_memory_health(stats)
        top_processes = self.get_top_memory_processes(5)
        
        report = {
            'timestamp': stats['timestamp'],
            'status': status,
            'severity': severity,
            'recommendations': recommendations,
            'memory': {
                'total_mb': round(stats['memory']['total'] / (1024*1024)),
                'used_mb': round(stats['memory']['used'] / (1024*1024)),
                'available_mb': round(stats['memory']['available'] / (1024*1024)),
                'percent': round(stats['memory']['percent'], 1),
                'buffers_cached_mb': round((stats['memory']['buffers'] + stats['memory']['cached']) / (1024*1024))
            },
            'swap': {
                'total_mb': round(stats['swap']['total'] / (1024*1024)),
                'used_mb': round(stats['swap']['used'] / (1024*1024)),
                'percent': round(stats['swap']['percent'], 1)
            },
            'vm_activity': {
                'pages_swapped_in': stats['vm_stats']['pswpin'],
                'pages_swapped_out': stats['vm_stats']['pswpout'],
                'page_faults': stats['vm_stats']['pgfault'],
                'major_page_faults': stats['vm_stats']['pgmajfault']
            },
            'top_processes': top_processes
        }
        
        return report
    
    def monitor_once(self) -> Dict:
        """Perform single monitoring cycle"""
        stats = self.get_memory_stats()
        report = self.generate_report(stats)
        
        # Perform automatic actions based on severity
        if report['severity'] == 'CRITICAL' and self.config.get('enable_auto_cleanup', True):
            cleanup_results = self.perform_memory_cleanup()
            report['cleanup_performed'] = cleanup_results
        
        # Optimize swap settings
        swap_optimization = self.optimize_swap_settings()
        if swap_optimization:
            report['swap_optimization'] = swap_optimization
        
        return report
    
    def monitor_continuous(self, interval: int = 60, max_iterations: Optional[int] = None):
        """Run continuous monitoring"""
        self.logger.info(f"Starting continuous memory monitoring (interval: {interval}s)")
        
        iteration = 0
        while True:
            try:
                report = self.monitor_once()
                
                # Log based on severity
                if report['severity'] == 'CRITICAL':
                    self.logger.critical(f"MEMORY CRITICAL: {report['status']}")
                elif report['severity'] == 'WARNING':
                    self.logger.warning(f"MEMORY WARNING: {report['status']}")
                else:
                    self.logger.info(f"Memory OK: {report['status']}")
                
                # Save detailed report to file
                report_file = self.log_dir / f"memory_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(report_file, 'w') as f:
                    json.dump(report, f, indent=2)
                
                iteration += 1
                if max_iterations and iteration >= max_iterations:
                    break
                
                time.sleep(interval)
                
            except KeyboardInterrupt:
                self.logger.info("Memory monitoring stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Monitoring error: {e}")
                time.sleep(interval)

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='TSV6 Memory Monitor')
    parser.add_argument('--config', '-c', help='Configuration file path')
    parser.add_argument('--once', '-o', action='store_true', help='Run once and exit')
    parser.add_argument('--interval', '-i', type=int, default=60, help='Monitoring interval (seconds)')
    parser.add_argument('--json', '-j', action='store_true', help='Output JSON format')
    parser.add_argument('--cleanup', action='store_true', help='Perform memory cleanup')
    
    args = parser.parse_args()
    
    monitor = MemoryMonitor(args.config)
    
    if args.cleanup:
        print("Performing memory cleanup...")
        results = monitor.perform_memory_cleanup()
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(f"Cleanup results: {results}")
        return
    
    if args.once:
        report = monitor.monitor_once()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Memory Status: {report['severity']} - {report['status']}")
            print(f"Memory: {report['memory']['used_mb']}MB/{report['memory']['total_mb']}MB ({report['memory']['percent']}%)")
            print(f"Swap: {report['swap']['used_mb']}MB/{report['swap']['total_mb']}MB ({report['swap']['percent']}%)")
            if report['recommendations']:
                print("Recommendations:")
                for rec in report['recommendations']:
                    print(f"  - {rec}")
    else:
        monitor.monitor_continuous(args.interval)

if __name__ == '__main__':
    main()
