#!/usr/bin/env python3
"""
Enhanced Error Recovery System for Production IoT Device

Provides comprehensive error handling, recovery mechanisms, and fallback
strategies for critical system components with staged escalation and 
persistent failure tracking across reboots.

Key improvements:
- Staged recovery escalation (soft -> intermediate -> hard -> system restart)
- Persistent failure tracking across reboots to detect hardware issues
- Lower system restart thresholds for critical components
- WiFi driver reset capability as intermediate recovery
- Enhanced logging and metrics
"""

import logging
import threading
import time
import traceback
import os
import json
import subprocess
from typing import Dict, Any, Callable, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum
from collections import deque
from pathlib import Path
from .filesystem_ops import sync_filesystem, atomic_write_json


class RecoveryAction(Enum):
    RESTART_COMPONENT = "restart_component"
    RESTART_SERVICE = "restart_service" 
    RESET_CONNECTION = "reset_connection"
    RELOAD_WIFI_DRIVER = "reload_wifi_driver"
    FALLBACK_MODE = "fallback_mode"
    SYSTEM_RESTART = "system_restart"
    ALERT_ONLY = "alert_only"


class EscalationLevel(Enum):
    SOFT = "soft"           # Component restart, connection reset
    INTERMEDIATE = "intermediate"  # Service restart, driver reload
    HARD = "hard"          # Fallback mode, major resets
    CRITICAL = "critical"  # System restart


@dataclass
class ErrorEvent:
    """Error event information"""
    timestamp: float
    component: str
    error_type: str
    error_message: str
    severity: str  # "low", "medium", "high", "critical"
    context: Dict[str, Any]
    recovery_attempted: bool = False
    recovery_successful: bool = False
    recovery_action: Optional[RecoveryAction] = None
    escalation_level: Optional[EscalationLevel] = None


@dataclass
class ComponentHealth:
    """Health status of a system component"""
    name: str
    status: str  # "healthy", "degraded", "failed", "recovering"
    error_count: int
    last_error: Optional[ErrorEvent]
    last_success: Optional[float]
    consecutive_failures: int
    total_restarts: int
    escalation_level: EscalationLevel = EscalationLevel.SOFT


@dataclass 
class PersistentFailureData:
    """Persistent failure data across reboots"""
    component: str
    total_failures: int
    total_restarts: int
    boot_failures: int  # Failures that occurred on current boot
    last_failure_time: float
    hardware_fault_suspected: bool = False
    restart_loop_detected: bool = False


class ErrorRecoverySystem:
    """Enhanced error recovery and fallback system with staged escalation"""
    
    PERSISTENCE_FILE = "/var/lib/tsv6/failure_tracking.json"
    
    def __init__(self, max_error_history: int = 1000):
        self.max_error_history = max_error_history
        self.error_history = deque(maxlen=max_error_history)
        self.component_health: Dict[str, ComponentHealth] = {}
        self.recovery_handlers: Dict[str, Callable] = {}
        self.fallback_handlers: Dict[str, Callable] = {}
        self.boot_time = time.time()
        
        # Initialize logging first
        self.logger = logging.getLogger(__name__)
        
        # Load persistent failure data
        self.persistent_failures = self._load_persistent_failures()
        
        # Enhanced recovery policies with staged escalation
        self.recovery_policies = {
            "aws_connection": {
                "soft_threshold": 2,
                "intermediate_threshold": 4,  
                "hard_threshold": 6,
                "critical_threshold": 8,
                "soft_recovery_delay": 15,
                "intermediate_recovery_delay": 30,
                "hard_recovery_delay": 60,
                "critical_recovery_delay": 120,
                "max_restarts_per_hour": 8,
                "max_boot_failures": 5,  # System restart after this many failures since boot
                "escalation_actions": {
                    EscalationLevel.SOFT: RecoveryAction.RESET_CONNECTION,
                    EscalationLevel.INTERMEDIATE: RecoveryAction.RESTART_SERVICE,
                    EscalationLevel.HARD: RecoveryAction.FALLBACK_MODE,
                    EscalationLevel.CRITICAL: RecoveryAction.SYSTEM_RESTART
                }
            },
            "network": {
                "soft_threshold": 2,
                "intermediate_threshold": 3,
                "hard_threshold": 5,
                "critical_threshold": 7,
                "soft_recovery_delay": 10,
                "intermediate_recovery_delay": 30,
                "hard_recovery_delay": 60,
                "critical_recovery_delay": 120,
                "max_restarts_per_hour": 6,
                "max_boot_failures": 4,
                "escalation_actions": {
                    EscalationLevel.SOFT: RecoveryAction.RESET_CONNECTION,
                    EscalationLevel.INTERMEDIATE: RecoveryAction.RELOAD_WIFI_DRIVER,
                    EscalationLevel.HARD: RecoveryAction.RESTART_SERVICE,
                    EscalationLevel.CRITICAL: RecoveryAction.SYSTEM_RESTART
                }
            },
            "lte_modem": {
                "soft_threshold": 2,        # 60s to first recovery (2 * 30s check interval)
                "intermediate_threshold": 4,  # 120s
                "hard_threshold": 6,        # 180s
                "critical_threshold": 10,   # 300s (5 min)
                "soft_recovery_delay": 15,
                "intermediate_recovery_delay": 30,
                "hard_recovery_delay": 60,
                "critical_recovery_delay": 120,
                "max_restarts_per_hour": 6,
                "max_boot_failures": 5,
                "escalation_actions": {
                    EscalationLevel.SOFT: RecoveryAction.RESET_CONNECTION,  # Re-register network
                    EscalationLevel.INTERMEDIATE: RecoveryAction.RESTART_COMPONENT,  # Restart PDP
                    EscalationLevel.HARD: RecoveryAction.RESTART_SERVICE,  # Modem restart
                    EscalationLevel.CRITICAL: RecoveryAction.SYSTEM_RESTART  # GPIO power cycle
                }
            },
            "barcode_scanner": {
                "soft_threshold": 5,
                "intermediate_threshold": 10,
                "hard_threshold": 15,
                "critical_threshold": 20,
                "soft_recovery_delay": 5,
                "intermediate_recovery_delay": 10,
                "hard_recovery_delay": 30,
                "critical_recovery_delay": 60,
                "max_restarts_per_hour": 12,
                "max_boot_failures": 10,
                "escalation_actions": {
                    EscalationLevel.SOFT: RecoveryAction.RESTART_COMPONENT,
                    EscalationLevel.INTERMEDIATE: RecoveryAction.RESTART_SERVICE,
                    EscalationLevel.HARD: RecoveryAction.FALLBACK_MODE,
                    EscalationLevel.CRITICAL: RecoveryAction.SYSTEM_RESTART
                }
            },
            "video_player": {
                "soft_threshold": 2,
                "intermediate_threshold": 4,
                "hard_threshold": 6,
                "critical_threshold": 8,
                "soft_recovery_delay": 3,
                "intermediate_recovery_delay": 10,
                "hard_recovery_delay": 20,
                "critical_recovery_delay": 60,
                "max_restarts_per_hour": 10,
                "max_boot_failures": 6,
                "escalation_actions": {
                    EscalationLevel.SOFT: RecoveryAction.RESTART_COMPONENT,
                    EscalationLevel.INTERMEDIATE: RecoveryAction.RESTART_SERVICE,
                    EscalationLevel.HARD: RecoveryAction.FALLBACK_MODE,
                    EscalationLevel.CRITICAL: RecoveryAction.SYSTEM_RESTART
                }
            },
            "system": {
                "soft_threshold": 1,
                "intermediate_threshold": 1,
                "hard_threshold": 1,
                "critical_threshold": 1,
                "soft_recovery_delay": 60,
                "intermediate_recovery_delay": 120,
                "hard_recovery_delay": 300,
                "critical_recovery_delay": 600,
                "max_restarts_per_hour": 3,
                "max_boot_failures": 2,
                "escalation_actions": {
                    EscalationLevel.SOFT: RecoveryAction.SYSTEM_RESTART,
                    EscalationLevel.INTERMEDIATE: RecoveryAction.SYSTEM_RESTART,
                    EscalationLevel.HARD: RecoveryAction.SYSTEM_RESTART,
                    EscalationLevel.CRITICAL: RecoveryAction.ALERT_ONLY  # Prevent restart loops
                }
            }
        }
        
        # Threading
        self._recovery_thread: Optional[threading.Thread] = None
        self._stop_recovery = threading.Event()
        self._recovery_queue = deque()
        self._recovery_lock = threading.Lock()
        
        # Check for restart loops on startup
        self._check_restart_loops()
        
        # Start recovery processor
        self._start_recovery_processor()
        
        print("🛡️ Enhanced Error Recovery System initialized")
        if self.persistent_failures:
            print(f"📊 Loaded {len(self.persistent_failures)} persistent failure records")
    
    def _load_persistent_failures(self) -> Dict[str, PersistentFailureData]:
        """Load persistent failure data from disk"""
        try:
            # Ensure the persistence directory exists
            persistence_dir = os.path.dirname(self.PERSISTENCE_FILE)
            os.makedirs(persistence_dir, exist_ok=True)
            
            if os.path.exists(self.PERSISTENCE_FILE):
                with open(self.PERSISTENCE_FILE, 'r') as f:
                    data = json.load(f)
                    
                # Convert dict back to PersistentFailureData objects
                result = {}
                for component, failure_dict in data.items():
                    result[component] = PersistentFailureData(**failure_dict)
                    # Reset boot failures counter on new boot
                    result[component].boot_failures = 0
                    
                return result
        except Exception as e:
            self.logger.warning(f"Could not load persistent failure data: {e}")
        
        return {}
    
    def _save_persistent_failures(self):
        """Save persistent failure data to disk using atomic writes"""
        try:
            # Convert PersistentFailureData objects to dict
            data = {}
            for component, failure_data in self.persistent_failures.items():
                data[component] = asdict(failure_data)
                
            # Use atomic write to prevent corruption
            if not atomic_write_json(self.PERSISTENCE_FILE, data):
                self.logger.error("Atomic write failed for persistent failure data")
        except Exception as e:
            self.logger.error(f"Failed to save persistent failure data: {e}")
    
    def _check_restart_loops(self):
        """Check for potential restart loops and take preventive action"""
        for component, failure_data in self.persistent_failures.items():
            # Check if we've had too many restarts recently
            if failure_data.last_failure_time > (self.boot_time - 300):  # Last 5 minutes
                if failure_data.total_restarts >= 5:
                    failure_data.restart_loop_detected = True
                    failure_data.hardware_fault_suspected = True
                    self.logger.critical(f"Restart loop detected for {component}!")
                    print(f"🚨 Restart loop detected for {component} - disabling auto-restart")
    
    def reload_wifi_driver(self) -> bool:
        """Reload WiFi driver as intermediate recovery step"""
        try:
            print("🔄 Reloading WiFi driver...")
            
            # Try to use systemd recovery manager if available
            # This is a fallback for when the network monitor doesn't have access
            try:
                from .systemd_recovery_manager import SystemdRecoveryManager
                recovery_manager = SystemdRecoveryManager()
                if recovery_manager.is_available():
                    print("🔧 Using systemd recovery manager for WiFi driver reload")
                    return recovery_manager.execute_intermediate_recovery()
            except ImportError:
                print("⚠️ Systemd recovery manager not available")
            
            # Fallback to direct commands (will likely fail without sudo)
            print("⚠️ Systemd recovery not available, attempting direct commands...")
            
            # Get current WiFi module
            result = subprocess.run(['lsmod'], capture_output=True, text=True)
            wifi_modules = []
            
            # Common WiFi driver modules
            common_modules = ['brcmfmac', 'brcmutil', 'cfg80211', 'wlan', 'rtl8192cu', 'rtl8xxxu']
            for line in result.stdout.splitlines():
                for module in common_modules:
                    if line.startswith(module):
                        wifi_modules.append(module)
            
            if not wifi_modules:
                # Try common Raspberry Pi WiFi modules
                wifi_modules = ['brcmfmac', 'brcmutil']
            
            # Unload modules (in reverse order)
            for module in reversed(wifi_modules):
                subprocess.run(['modprobe', '-r', module],
                             capture_output=True, timeout=10)
            
            time.sleep(2)
            
            # Reload modules
            for module in wifi_modules:
                subprocess.run(['modprobe', module],
                             capture_output=True, timeout=10)
            
            time.sleep(3)
            
            # Restart networking service
            subprocess.run(['systemctl', 'restart', 'networking'],
                         capture_output=True, timeout=30)
            
            time.sleep(5)
            print("✅ WiFi driver reload completed (may be incomplete)")
            return True
            
        except Exception as e:
            self.logger.error(f"WiFi driver reload failed: {e}")
            return False
    
    def register_component(self, name: str):
        """Register a component for monitoring"""
        self.component_health[name] = ComponentHealth(
            name=name,
            status="healthy",
            error_count=0,
            last_error=None,
            last_success=time.time(),
            consecutive_failures=0,
            total_restarts=0
        )
        
        # Initialize persistent failure data if not exists
        if name not in self.persistent_failures:
            self.persistent_failures[name] = PersistentFailureData(
                component=name,
                total_failures=0,
                total_restarts=0,
                boot_failures=0,
                last_failure_time=0
            )
        
        print(f"📝 Registered component: {name}")
    
    def register_recovery_handler(self, component: str, handler: Callable):
        """Register a recovery handler for a component"""
        self.recovery_handlers[component] = handler
        print(f"🔧 Recovery handler registered for: {component}")
    
    def register_fallback_handler(self, component: str, handler: Callable):
        """Register a fallback handler for a component"""
        self.fallback_handlers[component] = handler
        print(f"⚠️ Fallback handler registered for: {component}")
    
    def report_error(self, component: str, error_type: str, error_message: str, 
                    severity: str = "medium", context: Optional[Dict[str, Any]] = None):
        """Report an error for processing"""
        error_event = ErrorEvent(
            timestamp=time.time(),
            component=component,
            error_type=error_type,
            error_message=error_message,
            severity=severity,
            context=context or {}
        )
        
        # Update component health and persistent data
        self._update_component_health(component, error_event)
        self._update_persistent_failures(component, error_event)
        
        # Add to history
        self.error_history.append(error_event)
        
        # Queue for recovery processing
        with self._recovery_lock:
            self._recovery_queue.append(error_event)
        
        # Log the error
        log_level = {
            "low": logging.INFO,
            "medium": logging.WARNING,
            "high": logging.ERROR,
            "critical": logging.CRITICAL
        }.get(severity, logging.WARNING)
        
        self.logger.log(log_level, f"{component}: {error_type} - {error_message}")
        print(f"❌ Error reported: {component} - {error_message}")
    
    def report_success(self, component: str):
        """Report successful operation of a component"""
        if component in self.component_health:
            health = self.component_health[component]
            health.last_success = time.time()
            health.consecutive_failures = 0
            health.escalation_level = EscalationLevel.SOFT  # Reset escalation
            
            # Improve status if it was degraded
            if health.status in ["degraded", "recovering"]:
                health.status = "healthy"
                print(f"✅ Component recovered: {component}")
                
            # Update persistent data
            if component in self.persistent_failures:
                # Don't reset counters but note successful operation
                pass
    
    def _determine_escalation_level(self, component: str, health: ComponentHealth, 
                                  policy: Dict[str, Any]) -> EscalationLevel:
        """Determine appropriate escalation level based on failure count"""
        failures = health.consecutive_failures
        boot_failures = self.persistent_failures.get(component, PersistentFailureData(
            component=component, total_failures=0, total_restarts=0, 
            boot_failures=0, last_failure_time=0)).boot_failures
            
        # Check if we should escalate due to boot failures
        max_boot_failures = policy.get("max_boot_failures", 10)
        if boot_failures >= max_boot_failures:
            return EscalationLevel.CRITICAL
            
        # Determine level based on consecutive failures
        if failures >= policy.get("critical_threshold", 10):
            return EscalationLevel.CRITICAL
        elif failures >= policy.get("hard_threshold", 6):
            return EscalationLevel.HARD
        elif failures >= policy.get("intermediate_threshold", 4):
            return EscalationLevel.INTERMEDIATE
        else:
            return EscalationLevel.SOFT
    
    def _update_component_health(self, component: str, error: ErrorEvent):
        """Update component health based on error"""
        if component not in self.component_health:
            self.register_component(component)
        
        health = self.component_health[component]
        health.error_count += 1
        health.last_error = error
        health.consecutive_failures += 1
        
        # Determine escalation level
        policy = self.recovery_policies.get(component, self.recovery_policies.get("system", {}))
        health.escalation_level = self._determine_escalation_level(component, health, policy)
        error.escalation_level = health.escalation_level
        
        # Update status based on escalation level
        if health.escalation_level == EscalationLevel.CRITICAL:
            health.status = "failed"
        elif health.escalation_level in [EscalationLevel.HARD, EscalationLevel.INTERMEDIATE]:
            health.status = "degraded"
    
    def _update_persistent_failures(self, component: str, error: ErrorEvent):
        """Update persistent failure tracking"""
        if component not in self.persistent_failures:
            self.persistent_failures[component] = PersistentFailureData(
                component=component,
                total_failures=0,
                total_restarts=0,
                boot_failures=0,
                last_failure_time=0
            )
        
        failure_data = self.persistent_failures[component]
        failure_data.total_failures += 1
        failure_data.boot_failures += 1
        failure_data.last_failure_time = error.timestamp
        
        # Check for hardware fault indicators
        if failure_data.boot_failures >= 10 or failure_data.total_failures >= 50:
            failure_data.hardware_fault_suspected = True
            self.logger.warning(f"Hardware fault suspected for {component}")
        
        # Save to disk
        self._save_persistent_failures()
    
    def _start_recovery_processor(self):
        """Start the background recovery processor"""
        self._stop_recovery.clear()
        self._recovery_thread = threading.Thread(
            target=self._recovery_processor_loop,
            name="ErrorRecovery",
            daemon=True
        )
        self._recovery_thread.start()
    
    def _recovery_processor_loop(self):
        """Main recovery processing loop"""
        while not self._stop_recovery.is_set():
            try:
                # Process queued errors
                error_to_process = None
                with self._recovery_lock:
                    if self._recovery_queue:
                        error_to_process = self._recovery_queue.popleft()
                
                if error_to_process:
                    self._process_error_recovery(error_to_process)
                
                # Brief sleep to prevent busy waiting
                time.sleep(1)
                
            except Exception as e:
                self.logger.error(f"Error in recovery processor: {e}")
                self.logger.error(traceback.format_exc())
                time.sleep(5)
    
    def _process_error_recovery(self, error: ErrorEvent):
        """Process error and attempt recovery with staged escalation"""
        component = error.component
        policy = self.recovery_policies.get(component, self.recovery_policies.get("system", {}))
        health = self.component_health.get(component)
        
        if not health:
            return
        
        # Check for restart loop protection
        persistent_data = self.persistent_failures.get(component)
        if persistent_data and persistent_data.restart_loop_detected:
            self.logger.warning(f"Skipping recovery for {component} - restart loop detected")
            return
        
        # Check if we should attempt recovery
        if not self._should_attempt_recovery(component, health, policy):
            return
        
        # Mark recovery attempt
        error.recovery_attempted = True
        health.status = "recovering"
        
        escalation_level = health.escalation_level
        print(f"🔄 Attempting {escalation_level.value} recovery for: {component}")
        
        try:
            # Get recovery action for escalation level
            escalation_actions = policy.get("escalation_actions", {})
            recovery_action = escalation_actions.get(
                escalation_level, 
                RecoveryAction.RESTART_COMPONENT
            )
            error.recovery_action = recovery_action
            
            # Apply recovery delay based on escalation level
            delay_key = f"{escalation_level.value}_recovery_delay"
            recovery_delay = policy.get(delay_key, 10)
            
            if recovery_delay > 0:
                print(f"⏱️ Waiting {recovery_delay}s before {escalation_level.value} recovery...")
                time.sleep(recovery_delay)
            
            # Attempt recovery
            success = self._execute_recovery(component, recovery_action, error, escalation_level)
            
            if success:
                error.recovery_successful = True
                health.consecutive_failures = max(0, health.consecutive_failures - 2)  # Reward success
                health.status = "healthy"
                health.escalation_level = EscalationLevel.SOFT  # Reset escalation
                print(f"✅ {escalation_level.value} recovery successful for: {component}")
            else:
                # Recovery failed, escalate further next time
                print(f"❌ {escalation_level.value} recovery failed for: {component}")
                self._execute_fallback(component, error)
                
        except Exception as e:
            self.logger.error(f"Recovery attempt failed for {component}: {e}")
            self._execute_fallback(component, error)
    
    def _should_attempt_recovery(self, component: str, health: ComponentHealth, 
                                policy: Dict[str, Any]) -> bool:
        """Determine if recovery should be attempted"""
        # Check max restarts per hour
        max_restarts = policy.get("max_restarts_per_hour", 10)
        current_time = time.time()
        hour_ago = current_time - 3600
        
        recent_restarts = sum(1 for event in self.error_history 
                            if (event.component == component and 
                                event.recovery_attempted and 
                                event.timestamp > hour_ago))
        
        if recent_restarts >= max_restarts:
            print(f"🚫 Max restarts per hour reached for {component}: {recent_restarts}")
            return False
        
        # Check for hardware fault
        persistent_data = self.persistent_failures.get(component)
        if persistent_data and persistent_data.hardware_fault_suspected:
            if health.escalation_level != EscalationLevel.CRITICAL:
                print(f"🚫 Hardware fault suspected for {component} - limiting recovery")
                return False
        
        return True
    
    def _execute_recovery(self, component: str, action: RecoveryAction, 
                         error: ErrorEvent, escalation_level: EscalationLevel) -> bool:
        """Execute the recovery action"""
        try:
            # Handle special built-in actions
            if action == RecoveryAction.RELOAD_WIFI_DRIVER:
                return self.reload_wifi_driver()
            elif action == RecoveryAction.SYSTEM_RESTART:
                return self._execute_system_restart(component)
            
            # Use registered handler
            if component in self.recovery_handlers:
                return self.recovery_handlers[component](action, error, escalation_level)
            else:
                print(f"⚠️ No recovery handler for {component}")
                return False
        except Exception as e:
            self.logger.error(f"Recovery handler failed for {component}: {e}")
            return False
    
    def _execute_system_restart(self, component: str) -> bool:
        """Execute system restart with tracking"""
        print(f"🚨 System restart triggered by {component}")
        
        # Update persistent tracking
        if component in self.persistent_failures:
            self.persistent_failures[component].total_restarts += 1
            self._save_persistent_failures()
        
        # Log critical event
        self.logger.critical(f"System restart initiated due to {component} failures")
        
        try:
            # Give time for logging to flush
            time.sleep(2)
            
            # Sync filesystem before restart to prevent data corruption (Issue #21)
            sync_filesystem()
            
            subprocess.run(['reboot'], timeout=10)
            return True
        except Exception as e:
            self.logger.error(f"System restart failed: {e}")
            return False
    
    def _execute_fallback(self, component: str, error: ErrorEvent):
        """Execute fallback strategy"""
        print(f"⚠️ Executing fallback for: {component}")
        
        if component in self.fallback_handlers:
            try:
                self.fallback_handlers[component](error)
            except Exception as e:
                self.logger.error(f"Fallback handler failed for {component}: {e}")
        
        # Update health status
        if component in self.component_health:
            self.component_health[component].status = "failed"
    
    def get_system_health_status(self) -> Dict[str, Any]:
        """Get overall system health status with enhanced metrics"""
        total_components = len(self.component_health)
        healthy_count = sum(1 for h in self.component_health.values() 
                          if h.status == "healthy")
        degraded_count = sum(1 for h in self.component_health.values() 
                           if h.status == "degraded")
        failed_count = sum(1 for h in self.component_health.values() 
                         if h.status == "failed")
        
        # Determine overall status
        if failed_count > 0:
            overall_status = "critical"
        elif degraded_count > 0:
            overall_status = "degraded"
        else:
            overall_status = "healthy"
        
        # Add persistent failure info
        hardware_faults = sum(1 for pf in self.persistent_failures.values()
                            if pf.hardware_fault_suspected)
        restart_loops = sum(1 for pf in self.persistent_failures.values()
                          if pf.restart_loop_detected)
        
        return {
            "overall_status": overall_status,
            "total_components": total_components,
            "healthy": healthy_count,
            "degraded": degraded_count,
            "failed": failed_count,
            "hardware_faults_suspected": hardware_faults,
            "restart_loops_detected": restart_loops,
            "boot_time": self.boot_time,
            "components": {name: {
                "status": health.status,
                "error_count": health.error_count,
                "consecutive_failures": health.consecutive_failures,
                "escalation_level": health.escalation_level.value,
                "last_success": health.last_success,
                "total_restarts": health.total_restarts
            } for name, health in self.component_health.items()},
            "persistent_failures": {name: {
                "total_failures": pf.total_failures,
                "total_restarts": pf.total_restarts,
                "boot_failures": pf.boot_failures,
                "hardware_fault_suspected": pf.hardware_fault_suspected,
                "restart_loop_detected": pf.restart_loop_detected
            } for name, pf in self.persistent_failures.items()},
            "recent_errors": len([e for e in self.error_history 
                                if e.timestamp > time.time() - 3600])
        }
    
    def get_error_summary(self, hours: int = 1) -> Dict[str, Any]:
        """Get error summary for the last N hours"""
        cutoff_time = time.time() - (hours * 3600)
        recent_errors = [e for e in self.error_history if e.timestamp > cutoff_time]
        
        error_by_component = {}
        error_by_type = {}
        error_by_escalation = {}
        
        for error in recent_errors:
            # By component
            if error.component not in error_by_component:
                error_by_component[error.component] = 0
            error_by_component[error.component] += 1
            
            # By type
            if error.error_type not in error_by_type:
                error_by_type[error.error_type] = 0
            error_by_type[error.error_type] += 1
            
            # By escalation level
            if error.escalation_level:
                level = error.escalation_level.value
                if level not in error_by_escalation:
                    error_by_escalation[level] = 0
                error_by_escalation[level] += 1
        
        return {
            "time_period_hours": hours,
            "total_errors": len(recent_errors),
            "by_component": error_by_component,
            "by_type": error_by_type,
            "by_escalation_level": error_by_escalation,
            "recovery_success_rate": self._calculate_recovery_success_rate(recent_errors)
        }
    
    def _calculate_recovery_success_rate(self, errors: List[ErrorEvent]) -> float:
        """Calculate recovery success rate"""
        attempted_recoveries = [e for e in errors if e.recovery_attempted]
        if not attempted_recoveries:
            return 0.0
        
        successful_recoveries = [e for e in attempted_recoveries if e.recovery_successful]
        return len(successful_recoveries) / len(attempted_recoveries) * 100
    
    def stop(self):
        """Stop the error recovery system"""
        print("🛑 Stopping error recovery system...")
        self._stop_recovery.set()
        if self._recovery_thread:
            self._recovery_thread.join(timeout=5)
        
        # Save persistent data one final time
        self._save_persistent_failures()
        print("✅ Error recovery system stopped")
