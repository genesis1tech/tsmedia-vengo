"""
Tests for tsv6.ads.client — AdApiClient.
"""

from __future__ import annotations

import pytest
import httpx
import respx

from tsv6.ads.client import AdApiClient, AdPod, Advertisement
from tsv6.ads.config import AdConfig


BASE_URL = "https://test.tsssp.com"


@pytest.mark.asyncio
async def test_request_ad_pod_success(ad_config, mock_ad_request):
    """Happy path: server returns a filled ad pod."""
    async with AdApiClient(ad_config) as client:
        pod = await client.request_ad_pod()

    assert isinstance(pod, AdPod)
    assert len(pod.advertisements) == 1
    ad = pod.advertisements[0]
    assert isinstance(ad, Advertisement)
    assert ad.id == "ad_test_001"
    assert ad.length_in_seconds == 15
    assert ad.proof_of_play_url.endswith("/pop_001")


@pytest.mark.asyncio
async def test_request_ad_pod_no_fill(ad_config, mock_no_fill):
    """Server returns 204 — no eligible ad."""
    async with AdApiClient(ad_config) as client:
        pod = await client.request_ad_pod()

    assert pod is None


@pytest.mark.asyncio
async def test_post_proof_of_play(ad_config, mock_pop):
    """Proof-of-play is accepted (204)."""
    async with AdApiClient(ad_config) as client:
        await client.post_proof_of_play(
            f"{BASE_URL}/api/v1/proof_of_play/pop_001",
            {
                "played_at": "2026-04-18T14:22:31Z",
                "actual_duration_ms": 15020,
                "display_area_id": "main",
            },
        )
    # No exception = pass


@pytest.mark.asyncio
async def test_post_proof_of_play_replay_ignored(ad_config, mock_api):
    """409 Conflict (replay) is silently swallowed."""
    mock_api.post("/api/v1/proof_of_play/pop_replay").mock(
        return_value=httpx.Response(409)
    )
    async with AdApiClient(ad_config) as client:
        # Must not raise
        await client.post_proof_of_play(
            f"{BASE_URL}/api/v1/proof_of_play/pop_replay",
            {"played_at": "2026-04-18T00:00:00Z", "actual_duration_ms": 10, "display_area_id": "main"},
        )


@pytest.mark.asyncio
async def test_post_expiration(ad_config, mock_pop):
    """Expiration POST succeeds."""
    async with AdApiClient(ad_config) as client:
        await client.post_expiration(
            f"{BASE_URL}/api/v1/expiration/pop_001",
            {"reason": "preempted_by_recycling_event"},
        )


@pytest.mark.asyncio
async def test_retry_on_timeout(ad_config):
    """Client retries up to 3 times on TimeoutException then re-raises."""
    call_count = 0

    with respx.mock(base_url=BASE_URL) as router:
        def side_effect(request):
            nonlocal call_count
            call_count += 1
            raise httpx.ReadTimeout("timed out", request=request)

        router.post("/api/v1/ad_request").mock(side_effect=side_effect)

        async with AdApiClient(ad_config) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.request_ad_pod()

    assert call_count == 3


@pytest.mark.asyncio
async def test_x_device_key_header_sent(ad_config, mock_ad_request):
    """X-Device-Key header is included in every request."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        captured_headers: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(204)

        router.post("/api/v1/ad_request").mock(side_effect=capture)

        async with AdApiClient(ad_config) as client:
            await client.request_ad_pod()

    assert captured_headers.get("x-device-key") == "test-api-key"
