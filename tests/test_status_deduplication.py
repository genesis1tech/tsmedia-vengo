#!/usr/bin/env python3
"""
Test script for verifying AWS status publish deduplication mechanism.

This test simulates multiple processes calling publish_status() simultaneously
to verify that the inter-process lock and time-based deduplication work correctly.
"""

import sys
import os
import time
import tempfile
from pathlib import Path

# Add project paths
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / 'src'))

from tsv6.core.aws_resilient_manager import ResilientAWSManager, RetryConfig


def test_inter_process_lock():
    """Test that inter-process lock prevents duplicate publishes across processes"""
    print("\n=== Test 1: Inter-Process Lock Deduplication ===\n")
    
    # Create a temporary lock file for testing
    lock_file = "/tmp/tsv6-status-publish-test.lock"
    
    # Clean up any existing test lock
    if Path(lock_file).exists():
        Path(lock_file).unlink()
    
    try:
        # Create two mock AWS managers
        manager1 = _create_mock_manager("TSV6_TEST_1")
        manager2 = _create_mock_manager("TSV6_TEST_2")
        
        # Both managers should acquire the lock
        print(f"Manager 1 PID: {os.getpid()}")
        manager1._acquire_status_publish_lock()
        print(f"Manager 1 lock acquired: {manager1._status_publish_lock_handle is not None}")
        
        # Second manager should fail to acquire lock (different PID)
        print(f"Manager 2 PID: {os.getpid()}")
        manager2._acquire_status_publish_lock()
        print(f"Manager 2 lock acquired: {manager2._status_publish_lock_handle is not None}")
        
        # Both managers should try to publish status
        print("\nAttempting publishes...")
        result1 = manager1.publish_status()
        print(f"Manager 1 publish result: {result1}")
        
        result2 = manager2.publish_status()
        print(f"Manager 2 publish result: {result2}")
        
        # Only first manager should succeed (holds lock)
        if result1 and not result2:
            print("\n✅ PASS: Inter-process lock working - only lock holder published")
        elif not result1 and result2:
            print("\n❌ FAIL: Both managers published - lock not working")
        else:
            print("\n⚠️  PARTIAL: Unexpected results")
        
    finally:
        # Cleanup
        if Path(lock_file).exists():
            Path(lock_file).unlink()
        print(f"\nCleaned up test lock file: {lock_file}")


def test_time_based_deduplication():
    """Test that time-based deduplication prevents rapid duplicate publishes"""
    print("\n=== Test 2: Time-Based Deduplication ===\n")
    
    manager = _create_mock_manager("TSV6_TEST_TIME")
    
    # First publish should succeed
    print("Publish 1: First publish (should succeed)")
    result1 = manager.publish_status()
    print(f"Result 1: {result1}")
    
    # Second publish immediately (should be skipped - too soon)
    print("\nPublish 2: Second publish immediately (should be skipped)")
    result2 = manager.publish_status()
    print(f"Result 2: {result2}")
    
    # Wait 31 seconds and try again (should succeed)
    print("\nWaiting 31 seconds...")
    time.sleep(31)
    print("Publish 3: After 31 seconds (should succeed)")
    result3 = manager.publish_status()
    print(f"Result 3: {result3}")
    
    # Verify results
    if result1 and not result2 and result3:
        print("\n✅ PASS: Time-based deduplication working")
    elif result1 and result2:
        print("\n❌ FAIL: Time-based deduplication not working - rapid publishes not blocked")
    else:
        print("\n⚠️  PARTIAL: Unexpected results")


def _create_mock_manager(thing_name: str) -> ResilientAWSManager:
    """Create a mock AWS manager for testing without actual AWS connection"""
    # Create temporary cert files for testing
    temp_dir = tempfile.mkdtemp()
    
    cert_file = Path(temp_dir) / "test_cert.pem"
    key_file = Path(temp_dir) / "test_key.pem"
    ca_file = Path(temp_dir) / "test_ca.pem"
    
    # Create dummy cert files
    for cert_file_path in [cert_file, key_file, ca_file]:
        cert_file_path.write_text("DUMMY_CERT")
    
    try:
        manager = ResilientAWSManager(
            thing_name=thing_name,
            endpoint="test-endpoint.iot.us-east-1.amazonaws.com",
            cert_path=str(cert_file),
            key_path=str(key_file),
            ca_path=str(ca_file),
            retry_config=RetryConfig(initial_delay=0.1, max_delay=1.0),
            use_unique_client_id=True,
            lock_file="/tmp/tsv6-status-publish-test.lock"
        )
        return manager
    except Exception as e:
        print(f"Failed to create mock manager: {e}")
        raise


def main():
    """Run all tests"""
    print("=" * 60)
    print("AWS Status Publish Deduplication Tests")
    print("=" * 60)
    
    # Run tests
    test_inter_process_lock()
    test_time_based_deduplication()
    
    print("\n" + "=" * 60)
    print("Tests complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
