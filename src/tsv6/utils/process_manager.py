#!/usr/bin/env python3
"""
Process management utilities for preventing duplicate client IDs and multiple instances

Features:
- Unique client ID generation with UUID or process-based identifiers
- Process locking mechanism to prevent multiple instances
- Session tracking for debugging duplicate connections
"""

import os
import uuid
import socket
import time
import atexit
from pathlib import Path
from typing import Optional
import fcntl


class ClientIDGenerator:
    """Generate unique client IDs to prevent AWS DUPLICATE_CLIENTID errors"""
    
    @staticmethod
    def generate_unique_client_id(thing_name: str, use_uuid: bool = True) -> str:
        """
        Generate a unique client ID for AWS IoT connections.
        
        Args:
            thing_name: Base thing name from AWS IoT
            use_uuid: If True, use UUID-based generation; if False, use process ID
            
        Returns:
            Unique client ID suitable for AWS IoT connections
            
        Examples:
            # With UUID (recommended for production)
            >>> client_id = ClientIDGenerator.generate_unique_client_id("TS_6465BC2F")
            >>> # Returns something like: "TS_6465BC2F-a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
            
            # With process ID
            >>> client_id = ClientIDGenerator.generate_unique_client_id("TS_6465BC2F", use_uuid=False)
            >>> # Returns something like: "TS_6465BC2F-12345"
        """
        if use_uuid:
            unique_part = str(uuid.uuid4())
            return f"{thing_name}-{unique_part}"
        else:
            # Fallback to process ID if UUID not desired
            pid = os.getpid()
            hostname = socket.gethostname()
            return f"{thing_name}-{hostname}-{pid}"
    
    @staticmethod
    def generate_session_id() -> str:
        """Generate a unique session ID for tracking connections"""
        return str(uuid.uuid4())


class ProcessLock:
    """
    Process locking mechanism to prevent multiple instances of the same application
    from running simultaneously, which would cause DUPLICATE_CLIENTID errors.
    """
    
    def __init__(self, lock_file: Optional[str] = None):
        """
        Initialize process lock.
        
        Args:
            lock_file: Path to lock file. If None, uses /tmp/tsv6-app.lock
        """
        if lock_file is None:
            lock_file = "/tmp/tsv6-app.lock"
        
        self.lock_file = Path(lock_file)
        self.lock_file_handle = None
        self.acquired = False
        
        # Ensure parent directory exists
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
    
    def acquire(self, timeout: float = 5.0) -> bool:
        """
        Attempt to acquire the process lock.
        
        Args:
            timeout: How long to wait for lock (seconds)
            
        Returns:
            True if lock was acquired, False if another process holds the lock
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # Open the lock file (create if doesn't exist)
                self.lock_file_handle = open(self.lock_file, 'w')
                
                # Try to acquire exclusive lock (non-blocking)
                fcntl.flock(self.lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                
                # Write our PID to the lock file
                self.lock_file_handle.write(f"pid={os.getpid()}\n")
                self.lock_file_handle.write(f"time={time.time()}\n")
                self.lock_file_handle.flush()
                
                self.acquired = True
                
                # Register cleanup on exit
                atexit.register(self.release)
                
                print(f"✅ Process lock acquired: {self.lock_file}")
                return True
                
            except IOError:
                # Lock file is locked by another process
                try:
                    with open(self.lock_file, 'r') as f:
                        existing_pid = f.readline().strip()
                    print(f"⚠️  Another instance is running ({existing_pid})")
                except:
                    pass
                
                if time.time() - start_time < timeout:
                    time.sleep(0.5)
            except Exception as e:
                print(f"❌ Error acquiring process lock: {e}")
                return False
        
        print(f"❌ Failed to acquire process lock after {timeout}s")
        return False
    
    def release(self):
        """Release the process lock"""
        if self.lock_file_handle:
            try:
                fcntl.flock(self.lock_file_handle, fcntl.LOCK_UN)
                self.lock_file_handle.close()
                self.lock_file.unlink()
                self.acquired = False
                print(f"✅ Process lock released: {self.lock_file}")
            except Exception as e:
                print(f"⚠️  Error releasing process lock: {e}")
    
    def is_acquired(self) -> bool:
        """Check if lock is currently acquired"""
        return self.acquired
    
    def __enter__(self):
        """Context manager entry"""
        if not self.acquire():
            raise RuntimeError("Failed to acquire process lock - another instance may be running")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.release()


class DuplicateConnectionPrevention:
    """
    Comprehensive solution to prevent DUPLICATE_CLIENTID errors by:
    1. Generating unique client IDs
    2. Enforcing single-instance constraint
    3. Tracking connection attempts
    """
    
    _instance = None
    
    def __init__(self, thing_name: str, lock_file: Optional[str] = None):
        """
        Initialize duplicate connection prevention.
        
        Args:
            thing_name: AWS IoT thing name
            lock_file: Optional custom lock file path
        """
        self.thing_name = thing_name
        self.process_lock = ProcessLock(lock_file)
        self.client_id = None
        self.session_id = None
        self.lock_acquired = False
    
    def initialize(self) -> bool:
        """
        Initialize the prevention system (acquire lock and generate IDs).
        
        Returns:
            True if successful, False if another instance is running
        """
        # Try to acquire process lock
        if not self.process_lock.acquire():
            print("❌ Cannot initialize - another instance is already running")
            return False
        
        self.lock_acquired = True
        
        # Generate unique IDs
        self.client_id = ClientIDGenerator.generate_unique_client_id(self.thing_name, use_uuid=True)
        self.session_id = ClientIDGenerator.generate_session_id()
        
        print(f"🔧 Duplicate connection prevention initialized:")
        print(f"   └─ Thing Name: {self.thing_name}")
        print(f"   └─ Client ID: {self.client_id}")
        print(f"   └─ Session ID: {self.session_id}")
        
        return True
    
    def get_client_id(self) -> str:
        """Get the unique client ID"""
        if not self.client_id:
            raise RuntimeError("Prevention system not initialized - call initialize() first")
        return self.client_id
    
    def get_session_id(self) -> str:
        """Get the session ID"""
        if not self.session_id:
            raise RuntimeError("Prevention system not initialized - call initialize() first")
        return self.session_id
    
    def cleanup(self):
        """Clean up resources"""
        if self.lock_acquired:
            self.process_lock.release()
            self.lock_acquired = False
