"""
Shared pytest fixtures for tsv6.ads tests.

Uses respx to mock the ts-ssp HTTP API without any network I/O.
"""

from __future__ import annotations

import pytest
import respx
import httpx

from tsv6.ads.config import AdConfig, DisplayAreaConfig
from tsv6.ads.client import AdApiClient


BASE_URL = "https://test.tsssp.com"

AD_RESPONSE = {
    "advertisement": [
        {
            "id": "ad_test_001",
            "spot_id": "spot_test_001",
            "display_area_id": "main",
            "asset_url": f"{BASE_URL}/creatives/hero.mp4",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 800,
            "length_in_seconds": 15,
            "lease_expiry": 9999999999,
            "should_expire_after": 9999999999,
            "proof_of_play_url": f"{BASE_URL}/api/v1/proof_of_play/pop_001",
            "expiration_url": f"{BASE_URL}/api/v1/expiration/pop_001",
        }
    ]
}


@pytest.fixture
def ad_config() -> AdConfig:
    return AdConfig(
        endpoint=BASE_URL,
        network_id="topperstopper",
        device_id="TS_TEST0001",
        api_key="test-api-key",
        enabled=True,
        cache_dir="/tmp/tsv6_test_cache",
        cache_max_bytes=10_000_000,
        offline_db_path="/tmp/tsv6_test_impressions.db",
        offline_max_rows=100,
        prefetch_lead_seconds=5,
        display_area=DisplayAreaConfig(),
    )


@pytest.fixture
def mock_api():
    """Active respx mock router for the ts-ssp API."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def mock_ad_request(mock_api):
    """Registers a successful ad_request mock that returns AD_RESPONSE."""
    mock_api.post("/api/v1/ad_request").mock(
        return_value=httpx.Response(200, json=AD_RESPONSE)
    )
    return mock_api


@pytest.fixture
def mock_no_fill(mock_api):
    """Registers a 204 No Content mock for ad_request."""
    mock_api.post("/api/v1/ad_request").mock(
        return_value=httpx.Response(204)
    )
    return mock_api


@pytest.fixture
def mock_pop(mock_api):
    """Registers proof_of_play and expiration mocks."""
    mock_api.post("/api/v1/proof_of_play/pop_001").mock(
        return_value=httpx.Response(204)
    )
    mock_api.post("/api/v1/expiration/pop_001").mock(
        return_value=httpx.Response(204)
    )
    return mock_api
