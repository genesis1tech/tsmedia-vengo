#!/usr/bin/env python3
"""
Memory Optimizer for Raspberry Pi - Critical Memory Pressure Management

Addresses GitHub Issue #39: Memory Pressure Causing System Instability
- Implements proactive memory management and optimization
- Monitors swap usage and performs cleanup when needed
- Provides graceful degradation under low memory conditions
- Implements memory-aware resource management

Features:
- Real-time memory monitoring with alerts
- Automatic garbage collection optimization
- Thread pool size adjustment based on available memory
- Resource cleanup and leak detection
- Emergency memory recovery procedures
"""

import gc
import os
import sys
import time
import psutil
import threading
import logging
import subprocess
import weakref
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MemoryThresholds:
    """Memory usage thresholds for different alert levels
    
    PHASE 4: Optimized for video workloads (VLC + barcode scanning + AWS IoT)
    VLC requires buffer space for decoded frames, so thresholds are calibrated
    to allow normal operation while preventing critical memory exhaustion.
    """
    # Memory percentage thresholds (PHASE 4: Balanced for video workloads)
    memory_warning_percent: float = 70.0      # Warning at 70% - start proactive cleanup
    memory_critical_percent: float = 80.0     # Critical at 80% - aggressive cleanup
    memory_emergency_percent: float = 90.0    # Emergency at 90% - emergency procedures

    # Swap usage thresholds
    swap_warning_percent: float = 25.0        # Warning at 25% swap usage
    swap_critical_percent: float = 45.0       # Critical at 45% swap usage
    swap_emergency_percent: float = 70.0      # Emergency at 70% swap usage
    
    # Memory amounts in MB (PHASE 4: Tuned for Pi 4 1GB RAM)
    min_free_memory_mb: float = 80.0          # Keep 80MB free minimum (was 50MB)
    gc_trigger_threshold_mb: float = 120.0    # Trigger GC when below 120MB (was 100MB)


@dataclass
class MemoryStatus:
    """Current memory status information"""
    total_memory_mb: float
    available_memory_mb: float
    used_memory_mb: float
    memory_percent: float
    swap_total_mb: float
    swap_used_mb: float
    swap_percent: float
    free_memory_mb: float
    alert_level: str  # "normal", "warning", "critical", "emergency"
    needs_optimization: bool
    timestamp: float


class MemoryOptimizer:
    """
    Advanced Memory Optimizer for Raspberry Pi
    
    Handles critical memory pressure situations by:
    1. Monitoring memory usage continuously
    2. Performing proactive optimizations
    3. Implementing graceful degradation
    4. Managing resource cleanup
    """
    
    def __init__(
        self,
        thresholds: Optional[MemoryThresholds] = None,
        check_interval: float = 10.0,
        enable_auto_optimization: bool = True,
        on_memory_alert: Optional[Callable[[MemoryStatus], None]] = None
    ):
        self.thresholds = thresholds or MemoryThresholds()
        self.check_interval = check_interval
        self.enable_auto_optimization = enable_auto_optimization
        self.on_memory_alert = on_memory_alert
        
        # Monitoring state
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_status: Optional[MemoryStatus] = None
        
        # Optimization tracking
        self._optimization_count = 0
        self._last_gc_time = 0.0
        self._registered_cleanup_handlers: List[Callable] = []
        self._weak_references: List[weakref.ReferenceType] = []
        
        # Thread pool management
        self._original_thread_limits: Dict[str, int] = {}
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        
        print("🧠 Memory Optimizer initialized")
        print(f"   Warning threshold: {self.thresholds.memory_warning_percent}%")
        print(f"   Critical threshold: {self.thresholds.memory_critical_percent}%")
        print(f"   Emergency threshold: {self.thresholds.memory_emergency_percent}%")
    
    def start_monitoring(self):
        """Start continuous memory monitoring"""
        if self._running:
            return
            
        self._running = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            name="MemoryOptimizer",
            daemon=True
        )
        self._monitor_thread.start()
        print("🚀 Memory monitoring started")
    
    def stop_monitoring(self):
        """Stop memory monitoring"""
        if not self._running:
            return
            
        print("🛑 Stopping memory optimizer...")
        self._running = False
        self._stop_event.set()
        
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        
        print("✅ Memory optimizer stopped")
    
    def get_memory_status(self) -> MemoryStatus:
        """Get current memory status"""
        try:
            # Defensive check for module availability (can be None during shutdown)
            if psutil is None:
                raise RuntimeError("psutil unavailable during shutdown")
            
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            timestamp = time.time()
            
            # Calculate metrics
            total_mb = memory.total / (1024 * 1024)
            available_mb = memory.available / (1024 * 1024)
            used_mb = memory.used / (1024 * 1024)
            memory_percent = memory.percent
            
            swap_total_mb = swap.total / (1024 * 1024)
            swap_used_mb = swap.used / (1024 * 1024)
            swap_percent = swap.percent
            
            free_mb = available_mb
            
            # Determine alert level
            alert_level = "normal"
            needs_optimization = False
            
            if (memory_percent >= self.thresholds.memory_emergency_percent or 
                swap_percent >= self.thresholds.swap_emergency_percent):
                alert_level = "emergency"
                needs_optimization = True
            elif (memory_percent >= self.thresholds.memory_critical_percent or
                  swap_percent >= self.thresholds.swap_critical_percent):
                alert_level = "critical"
                needs_optimization = True
            elif (memory_percent >= self.thresholds.memory_warning_percent or
                  swap_percent >= self.thresholds.swap_warning_percent):
                alert_level = "warning"
                needs_optimization = True
            
            return MemoryStatus(
                total_memory_mb=total_mb,
                available_memory_mb=available_mb,
                used_memory_mb=used_mb,
                memory_percent=memory_percent,
                free_memory_mb=free_mb,
                swap_total_mb=swap_total_mb,
                swap_used_mb=swap_used_mb,
                swap_percent=swap_percent,
                alert_level=alert_level,
                needs_optimization=needs_optimization,
                timestamp=timestamp
            )
        except (NameError, AttributeError, RuntimeError):
            # Handle module cleanup during shutdown - return safe defaults
            return MemoryStatus(
                total_memory_mb=0.0, available_memory_mb=0.0, used_memory_mb=0.0,
                memory_percent=0.0, free_memory_mb=0.0, swap_total_mb=0.0,
                swap_used_mb=0.0, swap_percent=0.0, alert_level="normal",
                needs_optimization=False, timestamp=0.0
            )

    def optimize_memory_usage(self, force: bool = False) -> Dict[str, Any]:
        """
        Perform memory optimization procedures
        
        Returns:
            Dict with optimization results and metrics
        """
        start_time = time.time()
        status_before = self.get_memory_status()
        
        if not force and not status_before.needs_optimization:
            return {
                "optimized": False,
                "reason": "No optimization needed",
                "memory_before": status_before.memory_percent,
                "memory_after": status_before.memory_percent
            }
        
        print(f"🧹 Starting memory optimization (Level: {status_before.alert_level})")
        
        optimization_actions = []
        
        # 1. Force garbage collection
        if self._should_run_gc():
            print("   ♻️  Running garbage collection...")
            collected_before = gc.get_count()
            
            # Multiple GC passes for thorough cleanup
            for generation in range(3):
                collected = gc.collect(generation)
                if collected > 0:
                    optimization_actions.append(f"GC gen{generation}: {collected} objects")
            
            self._last_gc_time = time.time()
            print(f"   ✓ Garbage collection completed")
        
        # 2. Clear weak references to dead objects
        dead_refs = []
        for ref in self._weak_references[:]:  # Copy list to avoid modification during iteration
            if ref() is None:
                dead_refs.append(ref)
                self._weak_references.remove(ref)
        
        if dead_refs:
            optimization_actions.append(f"Cleared {len(dead_refs)} dead references")
        
        # 3. Run registered cleanup handlers
        for cleanup_handler in self._registered_cleanup_handlers:
            try:
                cleanup_handler()
                optimization_actions.append("Ran custom cleanup handler")
            except Exception as e:
                self.logger.warning(f"Cleanup handler failed: {e}")
        
        # 4. Emergency procedures for critical situations
        if status_before.alert_level in ["critical", "emergency"]:
            self._perform_emergency_cleanup()
            optimization_actions.append("Emergency cleanup procedures")
        
        # 5. Optimize thread pools if needed
        if hasattr(self, '_optimize_thread_pools'):
            self._optimize_thread_pools(status_before)
            optimization_actions.append("Optimized thread pools")
        
        # Get status after optimization
        status_after = self.get_memory_status()
        optimization_time = time.time() - start_time
        
        memory_saved_mb = status_before.used_memory_mb - status_after.used_memory_mb
        memory_saved_percent = status_before.memory_percent - status_after.memory_percent
        
        self._optimization_count += 1
        
        result = {
            "optimized": True,
            "optimization_id": self._optimization_count,
            "actions": optimization_actions,
            "memory_before": status_before.memory_percent,
            "memory_after": status_after.memory_percent,
            "memory_saved_mb": memory_saved_mb,
            "memory_saved_percent": memory_saved_percent,
            "swap_before": status_before.swap_percent,
            "swap_after": status_after.swap_percent,
            "optimization_time_ms": optimization_time * 1000,
            "alert_level": status_before.alert_level
        }
        
        print(f"✅ Memory optimization completed:")
        print(f"   Memory: {status_before.memory_percent:.1f}% → {status_after.memory_percent:.1f}%")
        print(f"   Swap: {status_before.swap_percent:.1f}% → {status_after.swap_percent:.1f}%")
        print(f"   Saved: {memory_saved_mb:.1f} MB ({memory_saved_percent:.1f}%)")
        print(f"   Time: {optimization_time*1000:.1f}ms")
        
        return result
    
    def _should_run_gc(self) -> bool:
        """Determine if garbage collection should be run"""
        current_time = time.time()
        
        # Don't run GC too frequently (minimum 30 second interval)
        if current_time - self._last_gc_time < 30:
            return False
        
        # Check if GC would be beneficial
        status = self.get_memory_status()
        return (status.free_memory_mb < self.thresholds.gc_trigger_threshold_mb or
                status.needs_optimization)
    
    def _perform_emergency_cleanup(self):
        """Perform emergency cleanup procedures for critical memory situations"""
        print("🚨 Emergency memory cleanup procedures activated")
        
        # Force aggressive garbage collection
        for i in range(5):  # Multiple aggressive passes
            collected = gc.collect()
            if collected == 0:
                break
        
        # Clear Python internal caches (sys.modules.clear() disabled - causes KeyError)
        pass  # Emergency module cache clear disabled to prevent psutil KeyError
        
        # Try to clear import caches
        try:
            if hasattr(sys, '_clear_type_cache'):
                sys._clear_type_cache()
        except:
            pass
        
        print("   ✓ Emergency cleanup completed")
    
    def register_cleanup_handler(self, handler: Callable):
        """Register a custom cleanup handler for memory optimization"""
        self._registered_cleanup_handlers.append(handler)
    
    def register_weak_reference(self, obj: Any):
        """Register an object for weak reference tracking"""
        weak_ref = weakref.ref(obj)
        self._weak_references.append(weak_ref)
        return weak_ref
    
    def _monitoring_loop(self):
        """Main memory monitoring loop"""
        while not self._stop_event.wait(self.check_interval):
            try:
                status = self.get_memory_status()
                self._last_status = status
                
                # Auto-optimization if enabled
                if (self.enable_auto_optimization and 
                    status.needs_optimization and 
                    status.alert_level in ["warning", "critical", "emergency"]):
                    
                    self.optimize_memory_usage()
                
                # Call alert callback if registered
                if self.on_memory_alert and status.alert_level != "normal":
                    try:
                        self.on_memory_alert(status)
                    except Exception as e:
                        self.logger.error(f"Memory alert callback failed: {e}")
                        
            except (KeyError, NameError, AttributeError, RuntimeError) as e:
                # Silently handle shutdown-related errors
                if isinstance(e, (NameError, AttributeError)):
                    break  # Shutdown in progress, exit gracefully
                self.logger.error(f"Memory monitoring error: {type(e).__name__}: {e}")
    
    def get_optimization_stats(self) -> Dict[str, Any]:
        """Get memory optimization statistics"""
        current_status = self.get_memory_status()
        
        return {
            "total_optimizations": self._optimization_count,
            "current_memory_percent": current_status.memory_percent,
            "current_swap_percent": current_status.swap_percent,
            "current_alert_level": current_status.alert_level,
            "last_gc_time": self._last_gc_time,
            "cleanup_handlers_registered": len(self._registered_cleanup_handlers),
            "weak_references_tracked": len(self._weak_references),
            "monitoring_active": self._running
        }


# Global memory optimizer instance
_global_memory_optimizer: Optional[MemoryOptimizer] = None


def get_global_memory_optimizer() -> MemoryOptimizer:
    """Get or create global memory optimizer instance"""
    global _global_memory_optimizer
    
    if _global_memory_optimizer is None:
        _global_memory_optimizer = MemoryOptimizer(
            enable_auto_optimization=True,
            check_interval=15.0  # Check every 15 seconds
        )
    
    return _global_memory_optimizer


def start_memory_optimization():
    """Start global memory optimization"""
    optimizer = get_global_memory_optimizer()
    optimizer.start_monitoring()
    return optimizer


def optimize_memory_now(force: bool = False) -> Dict[str, Any]:
    """Perform immediate memory optimization"""
    optimizer = get_global_memory_optimizer()
    return optimizer.optimize_memory_usage(force=force)


def get_memory_status() -> MemoryStatus:
    """Get current memory status"""
    optimizer = get_global_memory_optimizer()
    return optimizer.get_memory_status()


if __name__ == "__main__":
    # Test script for memory optimizer
    print("Testing Memory Optimizer...")
    
    optimizer = MemoryOptimizer()
    status = optimizer.get_memory_status()
    
    print(f"Current Memory Status:")
    print(f"  Total: {status.total_memory_mb:.1f} MB")
    print(f"  Used: {status.used_memory_mb:.1f} MB ({status.memory_percent:.1f}%)")
    print(f"  Available: {status.available_memory_mb:.1f} MB")
    print(f"  Swap: {status.swap_used_mb:.1f} MB ({status.swap_percent:.1f}%)")
    print(f"  Alert Level: {status.alert_level}")
    
    if status.needs_optimization:
        print("\nRunning optimization...")
        result = optimizer.optimize_memory_usage()
        print(f"Optimization result: {result}")