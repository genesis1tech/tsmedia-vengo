#!/usr/bin/env python3
"""Simple script to set servo to 0 degrees (closed position)"""

import sys
from pathlib import Path

# Add project paths
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))
sys.path.insert(0, str(project_root / 'src/tsv6/hardware/stservo/vendor'))

from scservo_sdk import PortHandler, sms_sts, SMS_STS_TORQUE_ENABLE
import time
import os

def main():
    # Auto-detect port
    ports = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyACM1']
    port = None
    for p in ports:
        if os.path.exists(p):
            port = p
            break

    if not port:
        print("Error: No serial port found")
        sys.exit(1)

    print(f"Using port: {port}")

    # Connect to servo
    port_handler = PortHandler(port)
    port_handler.baudrate = 1000000  # 1Mbps default for ST3020

    if not port_handler.openPort():
        print(f"Error: Failed to open port {port}")
        sys.exit(1)

    servo = sms_sts(port_handler)
    servo_id = 1

    # Enable torque
    servo.write1ByteTxRx(servo_id, SMS_STS_TORQUE_ENABLE, 1)

    # Set servo to closed position (calibrated)
    closed_position = 4039
    print(f"Setting servo to closed position ({closed_position})...")
    servo.WritePosEx(servo_id, closed_position, 0, 50)  # position, speed=max, acceleration=50

    time.sleep(1.0)  # Wait for movement

    # Read current position
    position, _, _ = servo.ReadPos(servo_id)
    print(f"Done - servo is at position {position}")

    port_handler.closePort()

if __name__ == "__main__":
    main()
