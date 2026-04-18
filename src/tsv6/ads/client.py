"""
HTTP client for the ts-ssp ad-serving API.

Wraps httpx.AsyncClient with:
- X-Device-Key authentication header
- 3-second timeout on all requests
- Tenacity retry: 3 attempts, exponential backoff 1 → 2 → 4 s
- Typed request/response dataclasses matching the OpenAPI schema

No blocking I/O touches the event loop — all methods are async.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tsv6.ads.config import AdConfig

logger = logging.getLogger(__name__)

_RETRY_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)

_RETRY_DECORATOR = retry(
    retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)

# ---------------------------------------------------------------------------
# Response dataclasses (mirrors OpenAPI Advertisement schema)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Advertisement:
    id: str
    spot_id: str
    display_area_id: str
    asset_url: str
    mime_type: str
    width: int
    height: int
    length_in_seconds: int
    lease_expiry: int
    should_expire_after: int
    proof_of_play_url: str
    expiration_url: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Advertisement":
        return cls(
            id=data["id"],
            spot_id=data["spot_id"],
            display_area_id=data["display_area_id"],
            asset_url=data["asset_url"],
            mime_type=data["mime_type"],
            width=int(data["width"]),
            height=int(data["height"]),
            length_in_seconds=int(data["length_in_seconds"]),
            lease_expiry=int(data["lease_expiry"]),
            should_expire_after=int(data["should_expire_after"]),
            proof_of_play_url=data["proof_of_play_url"],
            expiration_url=data["expiration_url"],
        )


@dataclass(frozen=True)
class AdPod:
    advertisements: list[Advertisement]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AdPod":
        ads = [Advertisement.from_dict(a) for a in data.get("advertisement", [])]
        return cls(advertisements=ads)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AdApiClient:
    """
    Async wrapper around httpx for the ts-ssp ad-serving endpoints.

    Lifecycle:
        client = AdApiClient(config)
        async with client:
            pod = await client.request_ad_pod()
    """

    _REQUEST_TIMEOUT = 3.0  # seconds

    def __init__(self, config: AdConfig) -> None:
        self._config = config
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AdApiClient":
        self._http = httpx.AsyncClient(
            base_url=self._config.endpoint,
            headers={
                "X-Device-Key": self._config.api_key,
                "Content-Type": "application/json",
            },
            timeout=self._REQUEST_TIMEOUT,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("AdApiClient must be used as an async context manager")
        return self._http

    @_RETRY_DECORATOR
    async def request_ad_pod(self) -> AdPod | None:
        """
        POST /api/v1/ad_request and return an AdPod, or None on 204 (no fill).

        Raises httpx exceptions after retries are exhausted.
        """
        cfg = self._config
        da = cfg.display_area
        payload: dict[str, Any] = {
            "network_id": cfg.network_id,
            "device_id": cfg.device_id,
            "direct_connection": False,
            "display_area": [
                {
                    "id": "main",
                    "width": da.width,
                    "height": da.height,
                    "allow_audio": da.allow_audio,
                    "supported_media": ["video/mp4", "image/jpeg", "image/png"],
                    "min_duration": da.min_duration,
                    "max_duration": da.max_duration,
                }
            ],
        }

        resp = await self._client().post("/api/v1/ad_request", json=payload)

        if resp.status_code == 204:
            logger.debug("Ad request returned 204 — no fill")
            return None

        resp.raise_for_status()
        return AdPod.from_dict(resp.json())

    @_RETRY_DECORATOR
    async def post_proof_of_play(
        self, url: str, payload: dict[str, Any]
    ) -> None:
        """
        POST to a server-issued proof_of_play_url.

        Args:
            url: Absolute URL from Advertisement.proof_of_play_url.
            payload: ProofOfPlay JSON body.
        """
        resp = await self._client().post(url, json=payload)
        if resp.status_code in (409, 410):
            logger.warning(
                "Proof-of-play rejected (status %s) — replay or expired",
                resp.status_code,
            )
            return
        resp.raise_for_status()
        logger.debug("Proof-of-play accepted for %s", url)

    @_RETRY_DECORATOR
    async def post_expiration(self, url: str, payload: dict[str, Any]) -> None:
        """
        POST to a server-issued expiration_url.

        Args:
            url: Absolute URL from Advertisement.expiration_url.
            payload: Expiration JSON body including ``reason``.
        """
        resp = await self._client().post(url, json=payload)
        resp.raise_for_status()
        logger.debug("Expiration posted to %s", url)
