import os, sys, pathlib, json
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
    fh = json.loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"]   == "upc_resolved"
    assert fh["datasource"]  == "go_upc"
    # WebP put should also write master_products
    _aws_clients["master"].put_item.assert_called_once()
    written = _aws_clients["master"].put_item.call_args[1]["Item"]
    assert written["barcode"] == "123"
    assert written["productImageWebp"].endswith("/123.webp")
    assert written["productImage"] == "https://x/y.png"   # source URL stays in productImage
