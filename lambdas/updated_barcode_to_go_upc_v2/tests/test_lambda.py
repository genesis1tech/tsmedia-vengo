import os, sys, pathlib, json, types
from decimal import Decimal
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from unittest.mock import MagicMock, patch
import pytest

@pytest.fixture(autouse=True)
def _aws_clients(monkeypatch):
    fake_ddb = MagicMock()
    fake_master   = MagicMock(name="master_products")
    fake_negative = MagicMock(name="barcode_negative_cache")
    fake_brand    = MagicMock(name="brand_playlists")
    fake_brand.get_item.return_value = {"Item": {"depositPlaylist":"tsv6_processing","productPlaylist":"tsv6_product_display"}}
    fake_ddb.Table.side_effect = lambda n: {"master_products":fake_master,"barcode_negative_cache":fake_negative,"brand_playlists":fake_brand}[n]
    fake_iot      = MagicMock(); fake_firehose = MagicMock(); fake_s3 = MagicMock()
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(resource=None, client=None))
    monkeypatch.setattr("boto3.resource", lambda name, **_: fake_ddb)
    monkeypatch.setattr("boto3.client",   lambda name, **_: {"iot-data":fake_iot,"firehose":fake_firehose,"s3":fake_s3}[name])
    monkeypatch.setenv("GO_UPC_API_KEY", "test-key")
    yield {"master":fake_master,"negative":fake_negative,"brand":fake_brand,"iot":fake_iot,"firehose":fake_firehose,"s3":fake_s3}

def _import():
    if "lambda_function" in sys.modules: del sys.modules["lambda_function"]
    import lambda_function
    return lambda_function

def test_goupc_resolved_publishes_openDoor_with_null_image(_aws_clients):
    lf = _import()
    with patch.object(lf, "_fetch_goupc",  return_value={"name":"Foo","brand":"Bar","category":"Beverages","imageUrl":"https://x/y.png"}), \
         patch.object(lf, "_convert_and_upload_webp", return_value="https://topper-stopper-bucket.s3.amazonaws.com/product-images-webp/123.webp"):
        resp = lf.lambda_handler({"thingName":"TS_X","barcode":"123","transactionId":"tx1"}, None)
    assert resp["returnAction"] == "openDoor"
    pub = _aws_clients["iot"].publish.call_args
    body = json.loads(pub[1]["payload"])
    assert body["productImage"] is None              # first scan: no image
    assert body["productImageOriginal"] == "https://x/y.png"
    assert body["productName"]  == "Foo"
    assert body["dataSource"]   == "go_upc"
    assert body["containerType"] == "plastic_bottle"
    assert body["containerConfidence"] == 0.35
    fh = json.loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"]   == "upc_resolved"
    assert fh["datasource"]  == "go_upc"
    # WebP put should also write master_products
    _aws_clients["master"].put_item.assert_called_once()
    written = _aws_clients["master"].put_item.call_args[1]["Item"]
    assert written["barcode"] == "123"
    assert written["productImageWebp"].endswith("/123.webp")
    assert written["productImage"] == "https://x/y.png"   # source URL stays in productImage
    assert written["containerType"] == "plastic_bottle"
    assert written["containerConfidence"] == Decimal("0.35")


def test_upc_nomatch_writes_negative_cache(_aws_clients):
    lf = _import()
    with patch.object(lf, "_fetch_goupc", return_value=None), \
         patch.object(lf, "_fetch_upcitemdb", return_value=None), \
         patch.object(lf, "_fetch_openfoodfacts", return_value=None), \
         patch.object(lf, "_fetch_usda", return_value=None):
        resp = lf.lambda_handler({"thingName":"TS_X","barcode":"000","transactionId":"tx2"}, None)
    assert resp["returnAction"] == "noMatch"
    assert resp["reason"] == "upc_nomatch"
    _aws_clients["negative"].put_item.assert_called_once()


def test_upc_error_does_not_write_negative_cache(_aws_clients):
    lf = _import()
    with patch.object(lf, "_fetch_goupc", side_effect=RuntimeError("boom")):
        resp = lf.lambda_handler({"thingName":"TS_X","barcode":"222","transactionId":"tx3"}, None)
    assert resp["returnAction"] == "noMatch"
    assert resp["reason"] == "upc_error"
    _aws_clients["negative"].put_item.assert_not_called()


def test_image_conversion_failure_skips_webp_field(_aws_clients):
    lf = _import()
    with patch.object(lf, "_fetch_goupc", return_value={"name":"Sparkling Water","brand":"Y","category":"Beverages","imageUrl":"https://broken/"}), \
         patch.object(lf, "_convert_and_upload_webp", return_value=None):
        lf.lambda_handler({"thingName":"TS_X","barcode":"333","transactionId":"tx4"}, None)
    written = _aws_clients["master"].put_item.call_args[1]["Item"]
    assert "productImageWebp" not in written
    assert written["productImage"] == "https://broken/"


def test_glass_cold_pressed_juice_returns_no_match_and_does_not_cache_master(_aws_clients):
    lf = _import()
    product = {
        "name": "Organic Cold Pressed Juice",
        "brand": "7 Select",
        "category": "Beverages",
        "description": "Organic cold pressed juice",
        "packaging": "glass bottle",
        "imageUrl": "https://x/juice.jpg",
    }
    with patch.object(lf, "_fetch_goupc", return_value=None), \
         patch.object(lf, "_fetch_upcitemdb", return_value=None), \
         patch.object(lf, "_fetch_openfoodfacts", return_value=product), \
         patch.object(lf, "_fetch_usda", return_value=None):
        resp = lf.lambda_handler({"thingName":"TS_X","barcode":"052548613136","transactionId":"tx5"}, None)

    assert resp["returnAction"] == "noMatch"
    assert resp["reason"] == "unsupported_container:glass_bottle"
    pub = _aws_clients["iot"].publish.call_args
    assert pub[1]["topic"] == "TS_X/noMatch"
    body = json.loads(pub[1]["payload"])
    assert body["reason"] == "unsupported_container:glass_bottle"

    _aws_clients["master"].put_item.assert_not_called()
    _aws_clients["negative"].put_item.assert_called_once()
    negative = _aws_clients["negative"].put_item.call_args[1]["Item"]
    assert negative["source"] == "unsupported_container:glass_bottle"

    fh = json.loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"] == "unsupported_container"
    assert fh["returnaction"] == "noMatch"
    assert fh["productname"] == "Organic Cold Pressed Juice"
    assert fh["productbrand"] == "7 Select"
    assert fh["containertype"] == "glass_bottle"
    assert fh["containerconfidence"] == 0.95
