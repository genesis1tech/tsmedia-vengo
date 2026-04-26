import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

LAMBDA_NAME    = "BarcodeRepoLookupV2"
LAMBDA_VERSION = os.getenv("AWS_LAMBDA_FUNCTION_VERSION", "$LATEST")
FIREHOSE_NAME  = "tsv6-scans-v2"
UPC_LAMBDA     = "UpdatedBarcodeToGoUPCV2"

DEFAULT_NO_ITEM        = "tsv6_no_item_detected"
DEFAULT_NO_MATCH       = "tsv6_no_match"
DEFAULT_BARCODE_NOT_QR = "tsv6_barcode_not_qr"
DEFAULT_DEPOSIT        = "tsv6_processing"
DEFAULT_PRODUCT        = "tsv6_product_display"

dynamodb = boto3.resource("dynamodb")
iot      = boto3.client("iot-data")
lambda_c = boto3.client("lambda")
firehose = boto3.client("firehose")

master_table   = dynamodb.Table("master_products")
negative_table = dynamodb.Table("barcode_negative_cache")
brand_table    = dynamodb.Table("brand_playlists")


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal): return float(o)
        return super().default(o)


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _publish(topic, payload):
    iot.publish(topic=topic, qos=1, payload=json.dumps(payload, cls=DecimalEncoder))


def _firehose_put(row):
    firehose.put_record(
        DeliveryStreamName=FIREHOSE_NAME,
        Record={"Data": (json.dumps(row, cls=DecimalEncoder) + "\n").encode()},
    )


def _row(*, txid, thing, barcode, event_type, return_action, latency_ms,
         product_name=None, product_brand=None, product_category=None, product_desc=None,
         product_image=None, product_image_original=None, container_type=None,
         container_confidence=None, data_source=None, qr_url=None,
         deposit_playlist=None, product_playlist=None, no_item_playlist=None,
         no_match_playlist=None, barcode_not_qr_playlist=None, reason=None):
    return {
        "transactionid": txid, "thingname": thing, "barcode": barcode,
        "scantimestamp": _now_iso(), "eventtype": event_type, "returnaction": return_action,
        "productname": product_name, "productbrand": product_brand,
        "productcategory": product_category, "productdesc": product_desc,
        "productimage": product_image, "productimageoriginal": product_image_original,
        "containertype": container_type, "containerconfidence": container_confidence,
        "datasource": data_source, "lookuplatencyms": latency_ms,
        "qrurl": qr_url,
        "depositplaylist": deposit_playlist, "productplaylist": product_playlist,
        "noitemplaylist": no_item_playlist, "nomatchplaylist": no_match_playlist,
        "barcodenotqrplaylist": barcode_not_qr_playlist,
        "reason": reason, "flowversion": "v2",
        "lambdaname": LAMBDA_NAME, "lambdaversion": LAMBDA_VERSION,
    }


def lambda_handler(event, _ctx):
    started = time.time()
    barcode = event.get("barcode")
    thing   = event.get("thingName")
    txid    = event.get("transactionId") or event.get("transactionID") or str(uuid.uuid4())

    try:
        if not barcode or not thing:
            raise ValueError("Missing required field: barcode and thingName are required")
    except Exception as e:
        err = {"statusCode": 500, "thingName": thing, "transactionId": txid, "error": str(e)}
        return err

    if "http://" in barcode or "https://" in barcode:
        payload = {
            "statusCode": 200, "returnAction": "QRcode",
            "thingName": thing, "transactionId": txid,
            "barcodeNotQrPlaylist": DEFAULT_BARCODE_NOT_QR,
        }
        _publish(f"{thing}/qrCode", payload)
        _firehose_put(_row(
            txid=txid, thing=thing, barcode=barcode,
            event_type="qr_detected", return_action="QRcode",
            barcode_not_qr_playlist=DEFAULT_BARCODE_NOT_QR,
            latency_ms=int((time.time() - started) * 1000),
        ))
        return payload
