#!/usr/bin/env python3
"""Servo calibration script - set open and closed positions manually"""

import sys
import os
from pathlib import Path

# Add project paths
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))
sys.path.insert(0, str(project_root / 'src/tsv6/hardware/stservo/vendor'))

from scservo_sdk import PortHandler, sms_sts, SMS_STS_TORQUE_ENABLE
import time
import re

CONTROLLER_FILE = project_root / 'src/tsv6/hardware/stservo/controller.py'

def find_servo_port():
    """Auto-detect servo port"""
    ports = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyACM1']
    for p in ports:
        if os.path.exists(p):
            return p
    return None

def read_position(servo):
    """Read current servo position"""
    position, _, _ = servo.ReadPos(1)
    return position

def update_controller_file(closed_pos, open_pos):
    """Update the controller.py file with new positions"""
    with open(CONTROLLER_FILE, 'r') as f:
        content = f.read()

    # Update open_position
    content = re.sub(
        r'open_position: int = \d+,\s*#.*',
        f'open_position: int = {open_pos},      # Open position (calibrated)',
        content
    )

    # Update closed_position
    content = re.sub(
        r'closed_position: int = \d+,\s*#.*',
        f'closed_position: int = {closed_pos},    # Closed position (calibrated)',
        content
    )

    with open(CONTROLLER_FILE, 'w') as f:
        f.write(content)

    print(f"Updated {CONTROLLER_FILE}")

def main():
    print("=" * 50)
    print("  Servo Calibration Script")
    print("=" * 50)
    print()

    # Find port
    port = find_servo_port()
    if not port:
        print("Error: No serial port found")
        sys.exit(1)

    print(f"Using port: {port}")

    # Connect
    port_handler = PortHandler(port)
    port_handler.baudrate = 1000000

    if not port_handler.openPort():
        print(f"Error: Failed to open port {port}")
        sys.exit(1)

    servo = sms_sts(port_handler)

    # Disable torque for manual movement
    print("\nDisabling torque - servo can now be moved manually")
    servo.write1ByteTxRx(1, SMS_STS_TORQUE_ENABLE, 0)

    # Calibrate CLOSED position
    print("\n" + "-" * 50)
    print("STEP 1: CLOSED POSITION")
    print("-" * 50)
    print("Move the servo to the CLOSED position manually.")
    input("Press Enter when ready...")

    closed_pos = read_position(servo)
    print(f"Closed position recorded: {closed_pos}")

    # Calibrate OPEN position
    print("\n" + "-" * 50)
    print("STEP 2: OPEN POSITION")
    print("-" * 50)
    print("Move the servo to the OPEN position manually.")
    input("Press Enter when ready...")

    open_pos = read_position(servo)
    print(f"Open position recorded: {open_pos}")

    # Summary
    print("\n" + "=" * 50)
    print("CALIBRATION SUMMARY")
    print("=" * 50)
    print(f"  Closed position: {closed_pos}")
    print(f"  Open position:   {open_pos}")
    print()

    # Confirm save
    save = input("Save these values to controller? (y/n): ").strip().lower()

    if save == 'y':
        update_controller_file(closed_pos, open_pos)
        print("\nCalibration saved!")

        # Test the positions
        test = input("\nTest the calibration? (y/n): ").strip().lower()
        if test == 'y':
            print("\nEnabling torque and testing...")
            servo.write1ByteTxRx(1, SMS_STS_TORQUE_ENABLE, 1)

            print("Moving to CLOSED position...")
            servo.WritePosEx(1, closed_pos, 0, 50)
            time.sleep(1.5)

            print("Waiting 2 seconds...")
            time.sleep(2.0)

            print("Moving to OPEN position...")
            servo.WritePosEx(1, open_pos, 0, 50)
            time.sleep(1.5)

            print("Waiting 3 seconds...")
            time.sleep(3.0)

            print("Moving to CLOSED position...")
            servo.WritePosEx(1, closed_pos, 0, 50)
            time.sleep(1.5)

            print("\nTest complete!")
    else:
        print("\nCalibration not saved.")

    # Cleanup
    port_handler.closePort()
    print("\nDone.")

if __name__ == "__main__":
    main()
