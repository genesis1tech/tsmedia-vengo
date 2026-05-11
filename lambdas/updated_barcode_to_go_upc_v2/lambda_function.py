import io, json, os, time, urllib.error, urllib.request, uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

LAMBDA_NAME    = "UpdatedBarcodeToGoUPCV2"
LAMBDA_VERSION = os.getenv("AWS_LAMBDA_FUNCTION_VERSION", "$LATEST")
FIREHOSE_NAME  = "tsv6-scans-v2"
WEBP_BUCKET    = "topper-stopper-bucket"
WEBP_PREFIX    = "product-images-webp"
NEGATIVE_TTL_DAYS = 30
MAX_IMAGE_BYTES = 5 * 1024 * 1024

DEFAULT_NO_ITEM        = "tsv6_no_item_detected"
DEFAULT_NO_MATCH       = "tsv6_no_match"
DEFAULT_DEPOSIT        = "tsv6_processing"
DEFAULT_PRODUCT        = "tsv6_product_display"
DEFAULT_CONTAINER_CONFIDENCE = 0.35

dynamodb = boto3.resource("dynamodb")
iot      = boto3.client("iot-data")
firehose = boto3.client("firehose")
s3       = boto3.client("s3")
master_table   = dynamodb.Table("master_products")
negative_table = dynamodb.Table("barcode_negative_cache")
brand_table    = dynamodb.Table("brand_playlists")

GO_UPC_API_KEY = os.environ.get("GO_UPC_API_KEY", "")
GO_UPC_API_URL = "https://go-upc.com/api/v1/code"
USDA_API_KEY   = os.environ.get("USDA_API_KEY", "")
UPCITEMDB_API_URL    = "https://api.upcitemdb.com/prod/trial/lookup"
OPENFOODFACTS_API_URL = "https://world.openfoodfacts.org/api/v2/product"
USDA_API_URL          = "https://api.nal.usda.gov/fdc/v1/foods/search"
USER_AGENT     = "tsv6-v2"


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal): return float(o)
        return super().default(o)


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _publish(topic, payload):
    iot.publish(topic=topic, qos=1, payload=json.dumps(payload, cls=DecimalEncoder))


def _firehose_put(row):
    firehose.put_record(DeliveryStreamName=FIREHOSE_NAME,
                        Record={"Data": (json.dumps(row, cls=DecimalEncoder)+"\n").encode()})


def _row(**k):
    base = {
        "transactionid": k["txid"], "thingname": k["thing"], "barcode": k.get("barcode"),
        "scantimestamp": _now_iso(), "eventtype": k["event_type"], "returnaction": k["return_action"],
        "productname": k.get("product_name"), "productbrand": k.get("product_brand"),
        "productcategory": k.get("product_category"), "productdesc": k.get("product_desc"),
        "productimage": k.get("product_image"), "productimageoriginal": k.get("product_image_original"),
        "containertype": k.get("container_type"), "containerconfidence": k.get("container_confidence"),
        "datasource": k.get("data_source"), "lookuplatencyms": k.get("latency_ms"),
        "qrurl": k.get("qr_url"), "depositplaylist": k.get("deposit_playlist"),
        "productplaylist": k.get("product_playlist"), "noitemplaylist": k.get("no_item_playlist"),
        "nomatchplaylist": k.get("no_match_playlist"), "barcodenotqrplaylist": k.get("barcode_not_qr_playlist"),
        "reason": k.get("reason"), "flowversion": "v2",
        "lambdaname": LAMBDA_NAME, "lambdaversion": LAMBDA_VERSION,
    }
    return base


def _resolve_brand_playlists(brand):
    item = brand_table.get_item(Key={"brand": brand or "*default*"}).get("Item")
    if not item:
        item = brand_table.get_item(Key={"brand": "*default*"}).get("Item") or {}
    return (item.get("depositPlaylist", DEFAULT_DEPOSIT),
            item.get("productPlaylist", DEFAULT_PRODUCT))


def _fetch_goupc(barcode):
    """Return {'name','brand','category','imageUrl'} or None.

    Raises on transient/network errors so the caller can route to the
    upc_error path without polluting the negative cache.
    """
    if not GO_UPC_API_KEY: return None
    req = urllib.request.Request(f"{GO_UPC_API_URL}/{barcode}?key={GO_UPC_API_KEY}",
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise
    p = data.get("product") or {}
    if not (p.get("name") or p.get("brand")): return None
    return {"name": p.get("name"), "brand": p.get("brand"),
            "category": p.get("category"), "description": p.get("description"),
            "imageUrl": p.get("imageUrl")}


def _fetch_upcitemdb(barcode):
    """Return product dict or None. Raises on transient errors."""
    req = urllib.request.Request(f"{UPCITEMDB_API_URL}?upc={barcode}",
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (404, 400): return None
        raise
    items = data.get("items") or []
    if data.get("total", len(items)) == 0 or not items:
        return None
    item = items[0]
    images = item.get("images") or []
    image_url = images[0] if images else None
    if not (item.get("title") or item.get("brand")): return None
    return {"name": item.get("title"), "brand": item.get("brand"),
            "category": item.get("category"), "description": item.get("description"),
            "imageUrl": image_url}


def _fetch_openfoodfacts(barcode):
    """Return product dict or None. Raises on transient errors."""
    req = urllib.request.Request(f"{OPENFOODFACTS_API_URL}/{barcode}.json",
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise
    if data.get("status") == 0:
        return None
    product = data.get("product") or {}
    image_url = (product.get("image_front_url")
                 or product.get("image_url")
                 or product.get("image_front_small_url"))
    name = product.get("product_name")
    brand = product.get("brands")
    if not (name or brand): return None
    return {"name": name, "brand": brand,
            "category": product.get("categories"),
            "description": product.get("generic_name"),
            "packaging": product.get("packaging"),
            "packagings": product.get("packagings"),
            "keywords": product.get("_keywords"),
            "imageUrl": image_url}


def _fetch_usda(barcode):
    """Return product dict or None. Returns None silently if API key unset."""
    if not USDA_API_KEY:
        return None
    url = (f"{USDA_API_URL}?api_key={USDA_API_KEY}"
           f"&query={barcode}&dataType=Branded&pageSize=5")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (404, 400): return None
        raise
    foods = data.get("foods") or []
    if not foods:
        return None
    best = None
    for food in foods:
        gtin = food.get("gtinUpc") or ""
        if gtin and (gtin == barcode or gtin.lstrip("0") == str(barcode).lstrip("0")):
            best = food
            break
    if not best:
        first = foods[0]
        if first.get("gtinUpc"):
            best = first
    if not best:
        return None
    name = best.get("description")
    brand = best.get("brandOwner") or best.get("brandName")
    if not (name or brand): return None
    return {"name": name, "brand": brand,
            "category": best.get("foodCategory"),
            "description": best.get("description"),
            "packageWeight": best.get("packageWeight"),
            "householdServingFullText": best.get("householdServingFullText"),
            "imageUrl": None}


def _infer_container(result):
    text = " ".join(str(result.get(k) or "") for k in (
        "name", "brand", "category", "description", "packaging", "packagings",
        "keywords", "packageWeight", "householdServingFullText",
    )).lower()

    if "glass bottle" in text or "glass-bottle" in text or "glass" in text:
        return "glass_bottle", 0.95
    if "plastic bottle" in text or "pet bottle" in text or "pet-bottle" in text:
        return "plastic_bottle", 0.9
    if " aluminum can" in f" {text}" or "aluminum-can" in text:
        return "can", 0.9
    if " can" in f" {text}" or text.endswith("can"):
        return "can", 0.85
    if "carton" in text or "tetra" in text:
        return "carton", 0.75
    if ("seven select" in text or "7-select" in text or "7 select" in text) and "cold pressed juice" in text:
        return "glass_bottle", 0.9
    if "cold pressed juice" in text:
        return "glass_bottle", 0.7
    if "energy drink" in text or "sparkling water" in text or "soda" in text:
        return "can", 0.65
    if "water" in text or "juice" in text or "beverage" in text or "drink" in text:
        return "plastic_bottle", DEFAULT_CONTAINER_CONFIDENCE
    return "unknown", 0.0


def _is_supported_container(container_type):
    normalized = str(container_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in {"can", "aluminum", "aluminum_can", "plastic", "plastic_bottle", "pet", "pet_bottle"}


def _unsupported_container_reason(container_type):
    return f"unsupported_container:{container_type or 'unknown'}"


def _convert_and_upload_webp(barcode, source_url):
    """Download source, convert to WebP, upload. Returns final WebP URL or None on any failure."""
    try:
        from PIL import Image
        with urllib.request.urlopen(source_url, timeout=5) as r:
            buf = r.read(MAX_IMAGE_BYTES + 1)
        if len(buf) > MAX_IMAGE_BYTES: return None
        img = Image.open(io.BytesIO(buf)).convert("RGB")
        out = io.BytesIO()
        img.save(out, "WEBP", quality=80, method=6)
        key = f"{WEBP_PREFIX}/{barcode}.webp"
        s3.put_object(Bucket=WEBP_BUCKET, Key=key, Body=out.getvalue(),
                      ContentType="image/webp",
                      CacheControl="public, max-age=31536000, immutable")
        return f"https://{WEBP_BUCKET}.s3.amazonaws.com/{key}"
    except Exception:
        return None


def _publish_no_match(thing, txid, barcode, reason, started, product=None):
    payload = {"statusCode":200,"returnAction":"noMatch","thingName":thing,
               "transactionId":txid,"barcode":barcode,"reason":reason,
               "noMatchPlaylist":DEFAULT_NO_MATCH}
    _publish(f"{thing}/noMatch", payload)
    event_type = "upc_nomatch" if reason == "upc_nomatch" else (
        "unsupported_container" if str(reason).startswith("unsupported_container") else "upc_error"
    )
    _firehose_put(_row(txid=txid, thing=thing, barcode=barcode,
                       event_type=event_type,
                       return_action="noMatch", reason=reason,
                       product_name=(product or {}).get("name"),
                       product_brand=(product or {}).get("brand"),
                       product_category=(product or {}).get("category"),
                       product_desc=(product or {}).get("description"),
                       product_image_original=(product or {}).get("imageUrl"),
                       container_type=(product or {}).get("containerType"),
                       container_confidence=(product or {}).get("containerConfidence"),
                       no_match_playlist=DEFAULT_NO_MATCH,
                       latency_ms=int((time.time()-started)*1000)))
    return payload


def lambda_handler(event, _ctx):
    started = time.time()
    barcode = event.get("barcode"); thing = event.get("thingName")
    txid    = event.get("transactionId") or event.get("transactionID") or str(uuid.uuid4())
    if not barcode or not thing:
        return {"statusCode":500,"thingName":thing,"transactionId":txid,"error":"Missing barcode/thingName"}

    try:
        result = None
        data_source = None
        for src, fn in (("go_upc",        _fetch_goupc),
                        ("upcitemdb",     _fetch_upcitemdb),
                        ("openfoodfacts", _fetch_openfoodfacts),
                        ("usda",          _fetch_usda)):
            result = fn(barcode)
            if result:
                data_source = src
                break

        if not result:
            negative_table.put_item(Item={"barcode": barcode,
                "expires_at": (datetime.now(timezone.utc)+timedelta(days=NEGATIVE_TTL_DAYS)).isoformat().replace("+00:00","Z"),
                "source": "upc_nomatch"})
            return _publish_no_match(thing, txid, barcode, "upc_nomatch", started)

        container_type, container_confidence = _infer_container(result)
        result["containerType"] = container_type
        result["containerConfidence"] = container_confidence
        if not _is_supported_container(container_type):
            reason = _unsupported_container_reason(container_type)
            negative_table.put_item(Item={"barcode": barcode,
                "expires_at": (datetime.now(timezone.utc)+timedelta(days=NEGATIVE_TTL_DAYS)).isoformat().replace("+00:00","Z"),
                "source": reason})
            return _publish_no_match(thing, txid, barcode, reason, started, product=result)

        deposit_pl, product_pl = _resolve_brand_playlists(result.get("brand"))
        qr_url = f"https://tsrewards--test.expo.app/hook?scanid={txid}&barcode={barcode}"
        payload = {
            "statusCode":200, "returnAction":"openDoor",
            "thingName":thing, "transactionId":txid, "barcode":barcode,
            "productName":     result.get("name"),
            "productBrand":    result.get("brand"),
            "productCategory": result.get("category"),
            "productDesc":     result.get("description"),
            "productImage":    None,                       # first scan = text only
            "productImageOriginal": result.get("imageUrl"),
            "containerType":   container_type,
            "containerConfidence": container_confidence,
            "qrUrl": qr_url,
            "depositPlaylist": deposit_pl,
            "productPlaylist": product_pl,
            "noItemPlaylist":  DEFAULT_NO_ITEM,
            "dataSource": data_source,
        }
        _publish(f"{thing}/openDoor", payload)
        _firehose_put(_row(txid=txid, thing=thing, barcode=barcode,
            event_type="upc_resolved", return_action="openDoor",
            product_name=payload["productName"], product_brand=payload["productBrand"],
            product_category=payload["productCategory"], product_desc=payload["productDesc"],
            product_image=None, product_image_original=payload["productImageOriginal"],
            container_type=payload["containerType"], container_confidence=payload["containerConfidence"],
            data_source=data_source, qr_url=qr_url,
            deposit_playlist=deposit_pl, product_playlist=product_pl,
            no_item_playlist=DEFAULT_NO_ITEM,
            latency_ms=int((time.time()-started)*1000)))

        webp_url = _convert_and_upload_webp(barcode, payload["productImageOriginal"])
        master_item = {
            "barcode": barcode,
            "productName": payload["productName"], "productBrand": payload["productBrand"],
            "productCategory": payload["productCategory"], "productDesc": payload["productDesc"],
            "productImage":    payload["productImageOriginal"],   # JPEG/PNG for V1 reads
            "productImageOriginal": payload["productImageOriginal"],
            "containerType": payload["containerType"],
            "containerConfidence": Decimal(str(payload["containerConfidence"])),
        }
        if webp_url: master_item["productImageWebp"] = webp_url
        master_table.put_item(Item=master_item)
        return payload
    except Exception as e:
        try:
            return _publish_no_match(thing, txid, barcode, "upc_error", started)
        except Exception:
            return {"statusCode":500,"thingName":thing,"transactionId":txid,"error":str(e)}
