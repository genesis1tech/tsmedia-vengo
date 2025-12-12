#!/usr/bin/env python3
"""
Filesystem Operations Utility
============================

Critical filesystem operations with data integrity guarantees.
Addresses Issue #21: Power loss data corruption prevention.

Functions:
- atomic_write_file: Atomic file write with sync
- sync_filesystem: Ensure filesystem durability
"""

import os
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Union, Dict, Any
import logging

logger = logging.getLogger(__name__)


def atomic_write_file(path: Union[str, Path], content: Union[str, bytes], encoding: str = 'utf-8') -> bool:
    """
    Write file atomically with sync for data integrity.
    
    Prevents corruption on power loss by:
    1. Writing to temporary file first
    2. Flushing and syncing to disk
    3. Atomically renaming to final location
    4. Syncing filesystem metadata
    
    Args:
        path: Target file path
        content: File content (string or bytes)
        encoding: Text encoding (default: utf-8)
    
    Returns:
        bool: True if successful, False otherwise
    """
    path = Path(path)
    
    try:
        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create temporary file in same directory as target
        # This ensures atomic rename is possible (same filesystem)
        temp_path = path.with_suffix('.tmp')
        
        # Write content to temporary file
        if isinstance(content, bytes):
            with open(temp_path, 'wb') as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk
        else:
            with open(temp_path, 'w', encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk
        
        # Atomically rename temporary file to target
        temp_path.rename(path)
        
        # Sync filesystem to ensure metadata is written
        subprocess.run(['sync'], timeout=5, check=True)
        
        logger.info(f"Atomically wrote file: {path}")
        return True
        
    except Exception as e:
        logger.error(f"Atomic write failed for {path}: {e}")
        
        # Clean up temporary file on error
        try:
            if temp_path.exists():
                temp_path.unlink()
        except:
            pass  # Best effort cleanup
            
        return False


def atomic_write_json(path: Union[str, Path], data: Dict[str, Any], indent: int = 2) -> bool:
    """
    Atomically write JSON data to file.
    
    Args:
        path: Target file path
        data: Dictionary to write as JSON
        indent: JSON formatting indent
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        json_content = json.dumps(data, indent=indent)
        return atomic_write_file(path, json_content)
    except Exception as e:
        logger.error(f"JSON atomic write failed for {path}: {e}")
        return False


def sync_filesystem() -> bool:
    """
    Sync filesystem to ensure durability.
    
    Forces all buffered writes to be written to disk.
    Critical before system restarts or power-sensitive operations.
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Use sync command with timeout to prevent hanging
        result = subprocess.run(['sync'], check=True, timeout=10)
        logger.info("Filesystem sync completed successfully")
        return True
        
    except subprocess.TimeoutExpired:
        logger.error("Filesystem sync timed out after 10 seconds")
        return False
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Filesystem sync failed: {e}")
        return False
        
    except Exception as e:
        logger.error(f"Unexpected error during filesystem sync: {e}")
        return False


def ensure_data_integrity(path: Union[str, Path]) -> bool:
    """
    Ensure data integrity for specific file/directory.
    
    Forces sync of specific file data and metadata.
    
    Args:
        path: File or directory path to sync
    
    Returns:
        bool: True if successful, False otherwise
    """
    path = Path(path)
    
    try:
        if not path.exists():
            logger.warning(f"Path does not exist for integrity check: {path}")
            return False
        
        if path.is_file():
            # Sync specific file
            with open(path, 'rb') as f:
                os.fsync(f.fileno())
        
        # Sync parent directory metadata
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        
        logger.info(f"Data integrity ensured for: {path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to ensure data integrity for {path}: {e}")
        return False


def create_backup_with_integrity(source: Union[str, Path], backup: Union[str, Path]) -> bool:
    """
    Create backup with data integrity guarantees.
    
    Args:
        source: Source file path
        backup: Backup file path
    
    Returns:
        bool: True if successful, False otherwise
    """
    source = Path(source)
    backup = Path(backup)
    
    try:
        if not source.exists():
            logger.error(f"Source file does not exist: {source}")
            return False
        
        # Read source file
        content = source.read_bytes()
        
        # Write backup atomically
        if atomic_write_file(backup, content):
            logger.info(f"Created integrity-safe backup: {source} -> {backup}")
            return True
        else:
            return False
            
    except Exception as e:
        logger.error(f"Backup creation failed: {source} -> {backup}: {e}")
        return False