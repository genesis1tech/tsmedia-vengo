#!/usr/bin/env python3
"""V2 end-to-end smoke test: scan -> publish (flowVersion=v2) -> openDoor/noMatch/qrCode/error.

Usage:
    python scripts/v2_smoke_test.py                        # use the physical scanner
    python scripts/v2_smoke_test.py --barcode 611269163452 # bypass scanner with explicit code
"""

import argparse
import datetime
import json
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "tsv6" / "hardware" / "stservo" / "vendor"))

from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

from scservo_sdk import PortHandler, sms_sts

CERTS_DIR = ROOT / "assets" / "certs"
DEVICE_CONFIG = json.loads((CERTS_DIR / "device-config.json").read_text())
THING_NAME = DEVICE_CONFIG["thingName"]
ENDPOINT = DEVICE_CONFIG["iotEndpoint"]
CERT = str(CERTS_DIR / "aws_cert_crt.pem")
KEY = str(CERTS_DIR / "aws_cert_private.pem")
CA = str(CERTS_DIR / "aws_cert_ca.pem")

SHADOW_TOPIC = f"$aws/things/{THING_NAME}/shadow/update"
OPEN_DOOR_TOPIC = f"{THING_NAME}/openDoor"
NO_MATCH_TOPIC = f"{THING_NAME}/noMatch"
QR_CODE_TOPIC = f"{THING_NAME}/qrCode"
ERROR_TOPIC = f"{THING_NAME}/error"

SERVO_PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6054653-if00"
SERVO_BAUD = 1000000
SERVO_ID = 1
CLOSED_POS = 2500
OPEN_POS = 1500
SERVO_SPEED = 1500
SERVO_ACCEL = 50
HOLD_OPEN_SEC = 2.0

SCAN_TIMEOUT = 90
RESPONSE_TIMEOUT = 15


class ServoBus:
    def __init__(self):
        self.port = PortHandler(SERVO_PORT)
        self.port.baudrate = SERVO_BAUD
        if not self.port.openPort():
            raise RuntimeError(f"Failed to open {SERVO_PORT}")
        self.servo = sms_sts(self.port)
        _, comm, _ = self.servo.ping(SERVO_ID)
        if comm != 0:
            self.port.closePort()
            raise RuntimeError(f"Servo ID {SERVO_ID} did not respond")
        self.servo.write1ByteTxRx(SERVO_ID, 40, 1)
        self._goto(CLOSED_POS)
        time.sleep(0.6)

    def _goto(self, pos):
        self.servo.WritePosEx(SERVO_ID, pos, SERVO_SPEED, SERVO_ACCEL)

    def open_then_close(self):
        print(f"Servo: opening (-> {OPEN_POS})")
        self._goto(OPEN_POS)
        time.sleep(0.6)
        print(f"Servo: holding open {HOLD_OPEN_SEC}s")
        time.sleep(HOLD_OPEN_SEC)
        print(f"Servo: closing (-> {CLOSED_POS})")
        self._goto(CLOSED_POS)
        time.sleep(0.6)

    def close(self):
        try:
            self._goto(CLOSED_POS)
            time.sleep(0.4)
            self.servo.write1ByteTxRx(SERVO_ID, 40, 0)
        finally:
            self.port.closePort()


def connect_mqtt():
    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)
    client_id = f"{THING_NAME}-v2-test-{uuid.uuid4().hex[:8]}"
    print(f"Connecting to {ENDPOINT} as {client_id}")
    conn = mqtt_connection_builder.mtls_from_path(
        endpoint=ENDPOINT,
        cert_filepath=CERT,
        pri_key_filepath=KEY,
        ca_filepath=CA,
        client_bootstrap=bootstrap,
        client_id=client_id,
        clean_session=True,
        keep_alive_secs=30,
    )
    conn.connect().result(timeout=20)
    print("MQTT connected")
    return conn


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--barcode", help="Use this barcode instead of the physical scanner")
    return p.parse_args()


def main():
    args = parse_args()
    response_event = threading.Event()
    response_holder = {}

    def make_handler(label):
        def handler(topic, payload, **_):
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                msg = {"raw": payload.decode("utf-8", errors="replace")}
            print(f"<- {label}: {json.dumps(msg, indent=2)}")
            response_holder["action"] = label
            response_holder["msg"] = msg
            response_event.set()
        return handler

    conn = connect_mqtt()
    for topic, label in [
        (OPEN_DOOR_TOPIC, "openDoor"),
        (NO_MATCH_TOPIC, "noMatch"),
        (QR_CODE_TOPIC, "qrCode"),
        (ERROR_TOPIC, "error"),
    ]:
        conn.subscribe(topic=topic, qos=mqtt.QoS.AT_LEAST_ONCE, callback=make_handler(label))[0].result(timeout=10)
        print(f"Subscribed: {topic}")

    servo = ServoBus()
    print("Servo ready at closed position")

    exit_code = 0
    try:
        if args.barcode:
            barcode = args.barcode
            print(f"Using --barcode override: {barcode}")
        else:
            from tsv6.hardware.barcode_reader import BarcodeReader
            reader = BarcodeReader(quiet=False)
            print(f"Scanner mode: {reader.scanner_mode}")
            print(f"Scan a barcode (timeout {SCAN_TIMEOUT}s)...")
            barcode = reader.scan_single(timeout_sec=SCAN_TIMEOUT)
            if not barcode:
                print("No barcode received.")
                return 1
            print(f"Scanned: {barcode}")

        transaction_id = str(uuid.uuid4())
        t_pub = time.monotonic()
        payload = {
            "state": {
                "reported": {
                    "thingName": THING_NAME,
                    "flowVersion": "v2",
                    "transactionID": transaction_id,
                    "barcode": barcode,
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "deviceType": "raspberry-pi",
                    "scannerType": "USB-HID-KBW",
                    "application": "tsv6-v2-smoke",
                }
            }
        }
        print(f"-> publish {SHADOW_TOPIC} (transactionID={transaction_id})")
        pub_future, _ = conn.publish(topic=SHADOW_TOPIC, payload=json.dumps(payload), qos=mqtt.QoS.AT_LEAST_ONCE)
        pub_future.result(timeout=10)
        print(f"Published. Waiting up to {RESPONSE_TIMEOUT}s for response...")

        if not response_event.wait(timeout=RESPONSE_TIMEOUT):
            print("No response from cloud within timeout.")
            exit_code = 2
        else:
            elapsed_ms = int((time.monotonic() - t_pub) * 1000)
            action = response_holder.get("action")
            print(f"Round-trip: {elapsed_ms}ms (action={action})")
            if action == "openDoor":
                servo.open_then_close()
            else:
                print(f"{action} received; servo will NOT move.")
                if action != "qrCode":
                    exit_code = 3
    finally:
        try:
            servo.close()
        except Exception as e:
            print(f"Servo cleanup error: {e}")
        try:
            conn.disconnect().result(timeout=10)
            print("MQTT disconnected")
        except Exception as e:
            print(f"MQTT disconnect error: {e}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
