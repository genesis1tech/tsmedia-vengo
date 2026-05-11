import os, sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

@pytest.fixture(autouse=True)
def _aws_clients(monkeypatch):
    """Replace boto3 resource/client with mocks so import-time wiring is safe."""
    fake_dynamodb = MagicMock()
    fake_master   = MagicMock(name="master_products")
    fake_negative = MagicMock(name="barcode_negative_cache")
    fake_brand    = MagicMock(name="brand_playlists")
    def table(name):
        return {"master_products": fake_master,
                "barcode_negative_cache": fake_negative,
                "brand_playlists": fake_brand}[name]
    fake_dynamodb.Table.side_effect = table

    fake_iot      = MagicMock(name="iot-data")
    fake_lambda   = MagicMock(name="lambda")
    fake_firehose = MagicMock(name="firehose")

    def fake_resource(name, **_):
        if name == "dynamodb": return fake_dynamodb
        raise AssertionError(name)
    def fake_client(name, **_):
        return {"iot-data": fake_iot, "lambda": fake_lambda, "firehose": fake_firehose}[name]

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(resource=None, client=None))
    monkeypatch.setattr("boto3.resource", fake_resource)
    monkeypatch.setattr("boto3.client",   fake_client)

    yield {
        "master":   fake_master, "negative": fake_negative, "brand": fake_brand,
        "iot":      fake_iot,    "lambda":   fake_lambda,   "firehose": fake_firehose,
    }

def _import():
    """Import lambda_function fresh after monkeypatch is in place."""
    if "lambda_function" in sys.modules: del sys.modules["lambda_function"]
    import lambda_function
    return lambda_function

def test_missing_barcode_returns_500(_aws_clients):
    lf = _import()
    resp = lf.lambda_handler({"thingName": "TS_X"}, None)
    assert resp["statusCode"] == 500
    assert "barcode" in resp.get("error", "").lower()

def test_qr_detection_publishes_qrCode_topic(_aws_clients):
    lf = _import()
    resp = lf.lambda_handler({"thingName": "TS_X", "barcode": "https://example.com/foo", "transactionId": "tx1"}, None)
    assert resp["returnAction"] == "QRcode"
    args, kwargs = _aws_clients["iot"].publish.call_args
    assert kwargs["topic"] == "TS_X/qrCode"
    body = __import__("json").loads(kwargs["payload"])
    assert body["barcodeNotQrPlaylist"] == "tsv6_barcode_not_qr"
    _aws_clients["firehose"].put_record.assert_called_once()
    fh_body = __import__("json").loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh_body["eventtype"] == "qr_detected"
    assert fh_body["returnaction"] == "QRcode"

def test_master_hit_publishes_openDoor_with_webp(_aws_clients):
    _aws_clients["master"].get_item.return_value = {"Item": {
        "barcode": "611269163452",
        "productName": "Red Bull Yellow",
        "productBrand": "Red Bull",
        "productCategory": "Beverages",
        "productDesc": "Tropical energy drink",
        "productImage":      "https://go-upc.s3.amazonaws.com/images/93437582.png",
        "productImageWebp":  "https://topper-stopper-bucket.s3.amazonaws.com/product-images-webp/611269163452.webp",
        "productImageOriginal": "https://go-upc.s3.amazonaws.com/images/93437582.png",
        "containerType": "can", "containerConfidence": Decimal("0.95"),
    }}
    _aws_clients["brand"].get_item.return_value = {"Item": {
        "brand": "Red Bull",
        "depositPlaylist": "tsv6_redbull_promo",
        "productPlaylist": "tsv6_redbull_product",
    }}
    lf = _import()
    resp = lf.lambda_handler({"thingName": "TS_X", "barcode": "611269163452", "transactionId": "tx2"}, None)
    assert resp["returnAction"] == "openDoor"
    pub = _aws_clients["iot"].publish.call_args
    assert pub[1]["topic"] == "TS_X/openDoor"
    body = __import__("json").loads(pub[1]["payload"])
    assert body["productImage"].endswith("/611269163452.webp")
    assert body["productImageOriginal"].endswith(".png")
    assert body["depositPlaylist"]   == "tsv6_redbull_promo"
    assert body["productPlaylist"]   == "tsv6_redbull_product"
    assert body["noItemPlaylist"]    == "tsv6_no_item_detected"
    assert body["qrUrl"].startswith("https://tsrewards--test.expo.app/hook?scanid=tx2&barcode=611269163452")
    fh = __import__("json").loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"] == "master_hit"
    assert fh["datasource"] == "master"

def test_negative_cache_hit_publishes_noMatch(_aws_clients):
    from datetime import datetime, timedelta, timezone
    _aws_clients["master"].get_item.return_value = {}  # miss
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    _aws_clients["negative"].get_item.return_value = {"Item": {"barcode": "999", "expires_at": future}}
    lf = _import()
    resp = lf.lambda_handler({"thingName":"TS_X","barcode":"999","transactionId":"tx3"}, None)
    assert resp["returnAction"] == "noMatch"
    pub = _aws_clients["iot"].publish.call_args
    assert pub[1]["topic"] == "TS_X/noMatch"
    body = __import__("json").loads(pub[1]["payload"])
    assert body["reason"] == "cached_nomatch"
    assert body["noMatchPlaylist"] == "tsv6_no_match"

def test_master_hit_glass_container_publishes_noMatch(_aws_clients):
    _aws_clients["master"].get_item.return_value = {"Item": {
        "barcode": "052548613136",
        "productName": "Organic Cold Pressed Juice",
        "productBrand": "7 Select",
        "productCategory": "Beverages",
        "productDesc": "Organic cold pressed juice",
        "productImage": "https://example.com/juice.jpg",
        "containerType": "glass_bottle",
        "containerConfidence": Decimal("0.95"),
    }}
    lf = _import()
    resp = lf.lambda_handler({"thingName":"TS_X","barcode":"052548613136","transactionId":"tx-glass"}, None)

    assert resp["returnAction"] == "noMatch"
    assert resp["reason"] == "unsupported_container:glass_bottle"
    pub = _aws_clients["iot"].publish.call_args
    assert pub[1]["topic"] == "TS_X/noMatch"
    body = __import__("json").loads(pub[1]["payload"])
    assert body["reason"] == "unsupported_container:glass_bottle"
    fh = __import__("json").loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"] == "unsupported_container"
    assert fh["returnaction"] == "noMatch"
    assert fh["productname"] == "Organic Cold Pressed Juice"
    assert fh["productbrand"] == "7 Select"
    assert fh["containertype"] == "glass_bottle"
    assert fh["containerconfidence"] == 0.95
    assert fh["datasource"] == "master"

def test_unsupported_container_negative_cache_preserves_reason(_aws_clients):
    from datetime import datetime, timedelta, timezone
    _aws_clients["master"].get_item.return_value = {}
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    _aws_clients["negative"].get_item.return_value = {"Item": {
        "barcode": "052548613136",
        "expires_at": future,
        "source": "unsupported_container:glass_bottle",
    }}
    lf = _import()
    resp = lf.lambda_handler({"thingName":"TS_X","barcode":"052548613136","transactionId":"tx-cached-glass"}, None)

    assert resp["returnAction"] == "noMatch"
    assert resp["reason"] == "unsupported_container:glass_bottle"
    pub = _aws_clients["iot"].publish.call_args
    assert pub[1]["topic"] == "TS_X/noMatch"
    body = __import__("json").loads(pub[1]["payload"])
    assert body["reason"] == "unsupported_container:glass_bottle"
    fh = __import__("json").loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"] == "unsupported_container_cached"
    assert fh["reason"] == "unsupported_container:glass_bottle"

def test_full_miss_invokes_upc_lambda(_aws_clients):
    _aws_clients["master"].get_item.return_value   = {}
    _aws_clients["negative"].get_item.return_value = {}
    lf = _import()
    resp = lf.lambda_handler({"thingName":"TS_X","barcode":"888","transactionId":"tx4"}, None)
    assert resp["returnAction"] == "forwardedToUPC"
    inv = _aws_clients["lambda"].invoke.call_args
    assert inv[1]["FunctionName"] == "UpdatedBarcodeToGoUPCV2"
    assert inv[1]["InvocationType"] == "Event"
    payload = __import__("json").loads(inv[1]["Payload"])
    assert payload == {"barcode":"888","thingName":"TS_X","transactionId":"tx4"}
    # No Firehose row from V1-side on the miss path; UPC lambda writes it.
    _aws_clients["firehose"].put_record.assert_not_called()

def test_internal_exception_publishes_error_topic(_aws_clients):
    _aws_clients["master"].get_item.side_effect = RuntimeError("boom")
    lf = _import()
    resp = lf.lambda_handler({"thingName":"TS_X","barcode":"777","transactionId":"tx5"}, None)
    assert resp["statusCode"] == 500
    pub = _aws_clients["iot"].publish.call_args
    assert pub[1]["topic"] == "TS_X/error"
    fh = __import__("json").loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"]    == "lambda_error"
    assert fh["returnaction"] == "error"
