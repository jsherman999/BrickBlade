"""Rebrickable API — used sparingly since the catalog is mirrored locally."""

from __future__ import annotations

from typing import Any

import httpx

from brickblade.clients.base import request_with_retry

BASE = "https://rebrickable.com/api/v3"


class RebrickableClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ValueError("REBRICKABLE_KEY required")
        self._owns = client is None
        self.http = client or httpx.Client(timeout=30.0)
        self.http.headers.setdefault("Authorization", f"key {api_key}")

    def close(self) -> None:
        if self._owns:
            self.http.close()

    def __enter__(self) -> RebrickableClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def get_set(self, set_num: str) -> dict[str, Any]:
        r = request_with_retry(self.http, "GET", f"{BASE}/lego/sets/{set_num}/")
        return r.json()

    def search_sets(self, query: str, page_size: int = 20) -> list[dict[str, Any]]:
        r = request_with_retry(
            self.http,
            "GET",
            f"{BASE}/lego/sets/",
            params={"search": query, "page_size": page_size},
        )
        return r.json().get("results", [])
