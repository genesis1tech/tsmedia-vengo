import threading
import time
from typing import Callable, Optional
import queue

class TaskManager:
    def __init__(self):
        self.tasks = {}
        self.running = False
        self.task_lock = threading.Lock()
        
    def add_task(self, name: str, target: Callable, interval: float = 0, daemon: bool = True, **kwargs):
        """Add a task to be managed"""
        with self.task_lock:
            if name in self.tasks:
                print(f"Task {name} already exists")
                return False
                
            task_info = {
                'target': target,
                'interval': interval,
                'daemon': daemon,
                'thread': None,
                'stop_event': threading.Event(),
                'kwargs': kwargs
            }
            
            self.tasks[name] = task_info
            return True
            
    def start_task(self, name: str) -> bool:
        """Start a specific task"""
        with self.task_lock:
            if name not in self.tasks:
                print(f"Task {name} not found")
                return False
                
            task_info = self.tasks[name]
            if task_info['thread'] and task_info['thread'].is_alive():
                print(f"Task {name} is already running")
                return False
                
            # Create wrapper function for interval-based tasks
            if task_info['interval'] > 0:
                def interval_wrapper():
                    while not task_info['stop_event'].is_set():
                        try:
                            task_info['target'](**task_info['kwargs'])
                        except Exception as e:
                            print(f"Error in task {name}: {e}")
                        
                        # Wait for interval or stop event
                        task_info['stop_event'].wait(task_info['interval'])
                        
                target_func = interval_wrapper
            else:
                def continuous_wrapper():
                    try:
                        task_info['target'](task_info['stop_event'], **task_info['kwargs'])
                    except Exception as e:
                        print(f"Error in task {name}: {e}")
                        
                target_func = continuous_wrapper
                
            # Start thread
            task_info['thread'] = threading.Thread(
                target=target_func,
                name=name,
                daemon=task_info['daemon']
            )
            task_info['thread'].start()
            print(f"Started task: {name}")
            return True
            
    def stop_task(self, name: str) -> bool:
        """Stop a specific task"""
        with self.task_lock:
            if name not in self.tasks:
                return False
                
            task_info = self.tasks[name]
            task_info['stop_event'].set()
            
            if task_info['thread'] and task_info['thread'].is_alive():
                task_info['thread'].join(timeout=5)
                if task_info['thread'].is_alive():
                    print(f"Warning: Task {name} did not stop gracefully")
                    
            print(f"Stopped task: {name}")
            return True
            
    def start_all_tasks(self):
        """Start all registered tasks"""
        self.running = True
        for name in self.tasks:
            self.start_task(name)
            
    def stop_all_tasks(self):
        """Stop all running tasks"""
        self.running = False
        for name in list(self.tasks.keys()):
            self.stop_task(name)
            
    def is_task_running(self, name: str) -> bool:
        """Check if a task is running"""
        with self.task_lock:
            if name not in self.tasks:
                return False
            task_info = self.tasks[name]
            return task_info['thread'] and task_info['thread'].is_alive()
            
    def get_task_status(self) -> dict:
        """Get status of all tasks"""
        status = {}
        with self.task_lock:
            for name, task_info in self.tasks.items():
                status[name] = {
                    'running': task_info['thread'] and task_info['thread'].is_alive(),
                    'interval': task_info['interval'],
                    'daemon': task_info['daemon']
                }
        return status