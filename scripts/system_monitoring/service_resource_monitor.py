#!/usr/bin/env python3
"""
Service Resource Monitor for TSV6 Raspberry Pi
Specifically monitors services identified in Issue #42 as resource-intensive
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
from typing import Dict, List, Optional, Tuple

class ServiceResourceMonitor:
    """Monitor resource usage of specific system services"""
    
    # Services identified in Issue #42 as resource-intensive
    MONITORED_SERVICES = [
        'accounts-daemon',
        'dbus-daemon',
        'systemd-journald',
        'systemd-logind',
        'NetworkManager',
        'dhcpcd'
    ]
    
    def __init__(self, log_dir: str = '/tmp/service_logs'):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True, parents=True)
        
        # Setup logging
        log_file = self.log_dir / 'service_monitor.log'
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Resource usage history
        self.history = []
        
    def get_service_pid(self, service_name: str) -> Optional[int]:
        """Get PID of a systemd service"""
        try:
            result = subprocess.run([
                'systemctl', 'show', service_name, '--property=MainPID'
            ], capture_output=True, text=True, check=True)
            
            for line in result.stdout.strip().split('\n'):
                if line.startswith('MainPID='):
                    pid = int(line.split('=')[1])
                    return pid if pid > 0 else None
        except (subprocess.CalledProcessError, ValueError):
            return None
        
        return None
    
    def get_process_by_name(self, name: str) -> List[psutil.Process]:
        """Find processes by name (for services that might have multiple processes)"""
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if name in proc.info['name'] or any(name in arg for arg in proc.info['cmdline']):
                    processes.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return processes
    
    def get_service_status(self, service_name: str) -> Dict:
        """Get comprehensive status of a service"""
        status = {
            'name': service_name,
            'timestamp': datetime.now().isoformat(),
            'active': False,
            'enabled': False,
            'processes': []
        }
        
        try:
            # Check if service is active
            result = subprocess.run([
                'systemctl', 'is-active', service_name
            ], capture_output=True, text=True)
            status['active'] = result.returncode == 0
            
            # Check if service is enabled
            result = subprocess.run([
                'systemctl', 'is-enabled', service_name
            ], capture_output=True, text=True)
            status['enabled'] = result.returncode == 0
            
            # Get service PID from systemctl
            main_pid = self.get_service_pid(service_name)
            
            # Get all processes related to this service
            processes = []
            
            if main_pid:
                try:
                    proc = psutil.Process(main_pid)
                    processes.append(proc)
                except psutil.NoSuchProcess:
                    pass
            
            # Also search by name for services with multiple processes
            processes.extend(self.get_process_by_name(service_name))
            
            # Remove duplicates
            seen_pids = set()
            unique_processes = []
            for proc in processes:
                if proc.pid not in seen_pids:
                    seen_pids.add(proc.pid)
                    unique_processes.append(proc)
            
            # Get resource usage for each process
            for proc in unique_processes:
                try:
                    proc_info = {
                        'pid': proc.pid,
                        'name': proc.name(),
                        'cpu_percent': proc.cpu_percent(interval=0.1),
                        'memory_info': proc.memory_info()._asdict(),
                        'memory_percent': proc.memory_percent(),
                        'create_time': proc.create_time(),
                        'status': proc.status(),
                        'num_threads': proc.num_threads(),
                        'cmdline': ' '.join(proc.cmdline())
                    }
                    
                    # Calculate memory in MB
                    proc_info['memory_mb'] = proc_info['memory_info']['rss'] / (1024 * 1024)
                    
                    status['processes'].append(proc_info)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                    
        except Exception as e:
            status['error'] = str(e)
            self.logger.error(f"Error getting status for {service_name}: {e}")
        
        # Calculate totals for the service
        if status['processes']:
            status['total_cpu_percent'] = sum(p['cpu_percent'] for p in status['processes'])
            status['total_memory_mb'] = sum(p['memory_mb'] for p in status['processes'])
            status['total_memory_percent'] = sum(p['memory_percent'] for p in status['processes'])
            status['process_count'] = len(status['processes'])
        else:
            status['total_cpu_percent'] = 0
            status['total_memory_mb'] = 0
            status['total_memory_percent'] = 0
            status['process_count'] = 0
            
        return status
    
    def monitor_all_services(self) -> Dict:
        """Monitor all services and return comprehensive report"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'system_info': self._get_system_info(),
            'services': {},
            'summary': {
                'total_cpu_percent': 0,
                'total_memory_mb': 0,
                'total_memory_percent': 0,
                'active_services': 0,
                'high_resource_services': []
            }
        }
        
        for service_name in self.MONITORED_SERVICES:
            service_status = self.get_service_status(service_name)
            report['services'][service_name] = service_status
            
            if service_status['active']:
                report['summary']['active_services'] += 1
                report['summary']['total_cpu_percent'] += service_status['total_cpu_percent']
                report['summary']['total_memory_mb'] += service_status['total_memory_mb']
                report['summary']['total_memory_percent'] += service_status['total_memory_percent']
                
                # Flag high resource usage (thresholds from Issue #42)
                if (service_status['total_cpu_percent'] > 5.0 or 
                    service_status['total_memory_mb'] > 50):
                    report['summary']['high_resource_services'].append({
                        'name': service_name,
                        'cpu_percent': round(service_status['total_cpu_percent'], 1),
                        'memory_mb': round(service_status['total_memory_mb'], 1)
                    })
        
        # Add to history
        self.history.append(report)
        
        # Keep only last 100 reports in memory
        if len(self.history) > 100:
            self.history = self.history[-100:]
            
        return report
    
    def _get_system_info(self) -> Dict:
        """Get basic system information"""
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        return {
            'total_memory_mb': round(memory.total / (1024 * 1024)),
            'available_memory_mb': round(memory.available / (1024 * 1024)),
            'memory_percent': memory.percent,
            'swap_total_mb': round(swap.total / (1024 * 1024)),
            'swap_used_mb': round(swap.used / (1024 * 1024)),
            'swap_percent': swap.percent,
            'cpu_count': psutil.cpu_count(),
            'load_average': os.getloadavg() if hasattr(os, 'getloadavg') else None
        }
    
    def generate_alert_report(self, report: Dict) -> Optional[Dict]:
        """Generate alert if resource usage is concerning"""
        alerts = []
        
        for service_info in report['summary']['high_resource_services']:
            severity = 'WARNING'
            
            # Critical thresholds based on Issue #42 observations
            if service_info['cpu_percent'] > 10 or service_info['memory_mb'] > 100:
                severity = 'CRITICAL'
            
            alerts.append({
                'service': service_info['name'],
                'severity': severity,
                'cpu_percent': service_info['cpu_percent'],
                'memory_mb': service_info['memory_mb'],
                'message': f"{service_info['name']} consuming {service_info['cpu_percent']}% CPU, {service_info['memory_mb']}MB RAM"
            })
        
        if alerts:
            return {
                'timestamp': report['timestamp'],
                'alert_count': len(alerts),
                'alerts': alerts,
                'system_impact': {
                    'total_monitored_cpu': round(report['summary']['total_cpu_percent'], 1),
                    'total_monitored_memory_mb': round(report['summary']['total_memory_mb'], 1),
                    'system_memory_percent': report['system_info']['memory_percent']
                }
            }
        
        return None
    
    def save_report(self, report: Dict, filename: Optional[str] = None):
        """Save report to file"""
        if not filename:
            filename = f"service_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        filepath = self.log_dir / filename
        with open(filepath, 'w') as f:
            json.dump(report, f, indent=2)
        
        return filepath
    
    def monitor_continuous(self, interval: int = 60, max_iterations: Optional[int] = None):
        """Run continuous monitoring"""
        self.logger.info(f"Starting continuous service monitoring (interval: {interval}s)")
        self.logger.info(f"Monitoring services: {', '.join(self.MONITORED_SERVICES)}")
        
        iteration = 0
        while True:
            try:
                report = self.monitor_all_services()
                
                # Check for alerts
                alert_report = self.generate_alert_report(report)
                if alert_report:
                    self.logger.warning(f"RESOURCE ALERT: {alert_report['alert_count']} services consuming high resources")
                    for alert in alert_report['alerts']:
                        self.logger.warning(f"  {alert['severity']}: {alert['message']}")
                
                # Log summary
                summary = report['summary']
                self.logger.info(f"Services: {summary['active_services']} active, "
                               f"CPU: {summary['total_cpu_percent']:.1f}%, "
                               f"Memory: {summary['total_memory_mb']:.1f}MB")
                
                # Save detailed report
                self.save_report(report)
                
                iteration += 1
                if max_iterations and iteration >= max_iterations:
                    break
                
                time.sleep(interval)
                
            except KeyboardInterrupt:
                self.logger.info("Service monitoring stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Monitoring error: {e}")
                time.sleep(interval)

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='TSV6 Service Resource Monitor')
    parser.add_argument('--once', '-o', action='store_true', help='Run once and exit')
    parser.add_argument('--interval', '-i', type=int, default=60, help='Monitoring interval (seconds)')
    parser.add_argument('--json', '-j', action='store_true', help='Output JSON format')
    parser.add_argument('--service', '-s', help='Monitor specific service only')
    parser.add_argument('--log-dir', '-l', default='/tmp/service_logs', help='Log directory')
    
    args = parser.parse_args()
    
    monitor = ServiceResourceMonitor(args.log_dir)
    
    if args.once:
        if args.service:
            # Monitor specific service
            status = monitor.get_service_status(args.service)
            if args.json:
                print(json.dumps(status, indent=2))
            else:
                print(f"Service: {status['name']}")
                print(f"Active: {status['active']}, Enabled: {status['enabled']}")
                print(f"Processes: {status['process_count']}")
                print(f"CPU: {status['total_cpu_percent']:.1f}%")
                print(f"Memory: {status['total_memory_mb']:.1f}MB ({status['total_memory_percent']:.1f}%)")
        else:
            # Monitor all services
            report = monitor.monitor_all_services()
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print("TSV6 Service Resource Monitor Report")
                print("=" * 40)
                print(f"Active Services: {report['summary']['active_services']}")
                print(f"Total CPU Usage: {report['summary']['total_cpu_percent']:.1f}%")
                print(f"Total Memory Usage: {report['summary']['total_memory_mb']:.1f}MB")
                
                if report['summary']['high_resource_services']:
                    print("\nHigh Resource Services:")
                    for svc in report['summary']['high_resource_services']:
                        print(f"  {svc['name']}: {svc['cpu_percent']:.1f}% CPU, {svc['memory_mb']:.1f}MB")
    else:
        monitor.monitor_continuous(args.interval)

if __name__ == '__main__':
    main()