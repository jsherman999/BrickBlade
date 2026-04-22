"""BrickLink API — OAuth1 signing with four static tokens."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from oauthlib.oauth1 import SIGNATURE_HMAC_SHA1, Client

from brickblade.clients.base import request_with_retry

BASE = "https://api.bricklink.com/api/store/v1"

Condition = Literal["N", "U"]
GuideType = Literal["sold", "stock"]
ItemType = Literal["SET", "PART", "MINIFIG", "GEAR", "BOOK", "CATALOG"]


class BrickLinkClient:
    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        token: str,
        token_secret: str,
        client: httpx.Client | None = None,
    ) -> None:
        for v, n in [
            (consumer_key, "consumer_key"),
            (consumer_secret, "consumer_secret"),
            (token, "token"),
            (token_secret, "token_secret"),
        ]:
            if not v:
                raise ValueError(f"BrickLink {n} required")
        self._signer = Client(
            client_key=consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=token,
            resource_owner_secret=token_secret,
            signature_method=SIGNATURE_HMAC_SHA1,
        )
        self._owns = client is None
        self.http = client or httpx.Client(timeout=30.0)

    def close(self) -> None:
        if self._owns:
            self.http.close()

    def __enter__(self) -> BrickLinkClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{BASE}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        signed_url, signed_headers, _ = self._signer.sign(url, http_method="GET")
        r = request_with_retry(self.http, "GET", signed_url, headers=signed_headers)
        payload = r.json()
        if payload.get("meta", {}).get("code", 200) >= 400:
            raise RuntimeError(f"BrickLink error: {payload.get('meta')}")
        return payload.get("data", {})

    def get_item(self, item_type: ItemType, no: str) -> dict[str, Any]:
        return self._get(f"/items/{item_type}/{no}")

    def get_price_guide(
        self,
        item_type: ItemType,
        no: str,
        *,
        new_or_used: Condition = "U",
        guide_type: GuideType = "sold",
        currency_code: str = "USD",
    ) -> dict[str, Any]:
        return self._get(
            f"/items/{item_type}/{no}/price",
            {
                "new_or_used": new_or_used,
                "guide_type": guide_type,
                "currency_code": currency_code,
            },
        )
