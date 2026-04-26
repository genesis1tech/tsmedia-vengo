import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

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
