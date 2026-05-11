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


def _resolve_brand_playlists(brand):
    item = brand_table.get_item(Key={"brand": brand or "*default*"}).get("Item")
    if not item:
        item = brand_table.get_item(Key={"brand": "*default*"}).get("Item") or {}
    return (
        item.get("depositPlaylist", DEFAULT_DEPOSIT),
        item.get("productPlaylist", DEFAULT_PRODUCT),
    )


def _is_supported_container(container_type):
    normalized = str(container_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in {"can", "aluminum", "aluminum_can", "plastic", "plastic_bottle", "pet", "pet_bottle"}


def _unsupported_container_reason(container_type):
    return f"unsupported_container:{container_type or 'unknown'}"


def _process(event, started, barcode, thing, txid):
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

    item = master_table.get_item(Key={"barcode": barcode}).get("Item")
    if item:
        container_type = item.get("containerType")
        container_confidence = float(item.get("containerConfidence", 0) or 0)
        if not _is_supported_container(container_type):
            payload = {
                "statusCode": 200, "returnAction": "noMatch",
                "thingName": thing, "transactionId": txid, "barcode": barcode,
                "reason": _unsupported_container_reason(container_type),
                "noMatchPlaylist": DEFAULT_NO_MATCH,
            }
            _publish(f"{thing}/noMatch", payload)
            _firehose_put(_row(
                txid=txid, thing=thing, barcode=barcode,
                event_type="unsupported_container", return_action="noMatch",
                product_name=item.get("productName"), product_brand=item.get("productBrand"),
                product_category=item.get("productCategory"), product_desc=item.get("productDesc"),
                product_image=item.get("productImageWebp") or item.get("productImage"),
                product_image_original=item.get("productImageOriginal") or item.get("productImage"),
                container_type=container_type, container_confidence=container_confidence,
                data_source="master", no_match_playlist=DEFAULT_NO_MATCH,
                reason=payload["reason"],
                latency_ms=int((time.time() - started) * 1000),
            ))
            return payload

        deposit_pl, product_pl = _resolve_brand_playlists(item.get("productBrand"))
        wire_image = item.get("productImageWebp") or item.get("productImage")
        qr_url = f"https://tsrewards--test.expo.app/hook?scanid={txid}&barcode={barcode}"
        payload = {
            "statusCode": 200, "returnAction": "openDoor",
            "thingName": thing, "transactionId": txid, "barcode": barcode,
            "productName":     item.get("productName"),
            "productBrand":    item.get("productBrand"),
            "productCategory": item.get("productCategory"),
            "productDesc":     item.get("productDesc"),
            "productImage":    wire_image,
            "productImageOriginal": item.get("productImageOriginal") or item.get("productImage"),
            "containerType":   container_type,
            "containerConfidence": container_confidence,
            "qrUrl": qr_url,
            "depositPlaylist": deposit_pl,
            "productPlaylist": product_pl,
            "noItemPlaylist":  DEFAULT_NO_ITEM,
            "dataSource": "master",
        }
        _publish(f"{thing}/openDoor", payload)
        _firehose_put(_row(
            txid=txid, thing=thing, barcode=barcode,
            event_type="master_hit", return_action="openDoor",
            product_name=payload["productName"], product_brand=payload["productBrand"],
            product_category=payload["productCategory"], product_desc=payload["productDesc"],
            product_image=payload["productImage"], product_image_original=payload["productImageOriginal"],
            container_type=payload["containerType"], container_confidence=payload["containerConfidence"],
            data_source="master", qr_url=qr_url,
            deposit_playlist=deposit_pl, product_playlist=product_pl,
            no_item_playlist=DEFAULT_NO_ITEM,
            latency_ms=int((time.time() - started) * 1000),
        ))
        return payload

    neg = negative_table.get_item(Key={"barcode": barcode}).get("Item")
    if neg:
        valid = True
        if "expires_at" in neg:
            try:
                exp = datetime.fromisoformat(neg["expires_at"].replace("Z", "+00:00"))
                valid = datetime.now(timezone.utc) < exp
            except Exception:
                valid = True
        if valid:
            source = neg.get("source") or "cached_nomatch"
            reason = source if str(source).startswith("unsupported_container") else "cached_nomatch"
            event_type = "unsupported_container_cached" if reason.startswith("unsupported_container") else "nomatch_cached"
            payload = {
                "statusCode": 200, "returnAction": "noMatch",
                "thingName": thing, "transactionId": txid, "barcode": barcode,
                "reason": reason,
                "noMatchPlaylist": DEFAULT_NO_MATCH,
            }
            _publish(f"{thing}/noMatch", payload)
            _firehose_put(_row(
                txid=txid, thing=thing, barcode=barcode,
                event_type=event_type, return_action="noMatch",
                no_match_playlist=DEFAULT_NO_MATCH, reason=reason,
                latency_ms=int((time.time() - started) * 1000),
            ))
            return payload

    lambda_c.invoke(
        FunctionName=UPC_LAMBDA, InvocationType="Event",
        Payload=json.dumps({"barcode": barcode, "thingName": thing, "transactionId": txid}),
    )
    return {"statusCode": 200, "returnAction": "forwardedToUPC",
            "thingName": thing, "transactionId": txid, "barcode": barcode}


def lambda_handler(event, _ctx):
    started = time.time()
    barcode = event.get("barcode")
    thing   = event.get("thingName")
    txid    = event.get("transactionId") or event.get("transactionID") or str(uuid.uuid4())

    if not barcode or not thing:
        return {"statusCode": 500, "thingName": thing, "transactionId": txid,
                "error": "Missing required field: barcode and thingName are required"}

    try:
        return _process(event, started, barcode, thing, txid)
    except Exception as e:
        _publish(f"{thing}/error", {"statusCode": 500, "thingName": thing,
                                    "transactionId": txid, "error": str(e)})
        try:
            _firehose_put(_row(
                txid=txid, thing=thing, barcode=barcode,
                event_type="lambda_error", return_action="error",
                reason=str(e),
                latency_ms=int((time.time() - started) * 1000),
            ))
        except Exception:
            pass
        return {"statusCode": 500, "thingName": thing, "transactionId": txid, "error": str(e)}
