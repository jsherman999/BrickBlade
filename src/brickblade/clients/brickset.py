"""Brickset v3 — catalog, UPC/EAN lookup, and sealed price."""

from __future__ import annotations

import json
from typing import Any

import httpx

from brickblade.clients.base import request_with_retry

BASE = "https://brickset.com/api/v3.asmx"


class BricksetClient:
    def __init__(
        self,
        api_key: str,
        user_hash: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("BRICKSET_KEY required")
        self.api_key = api_key
        self.user_hash = user_hash
        self._owns = client is None
        self.http = client or httpx.Client(timeout=30.0)

    def close(self) -> None:
        if self._owns:
            self.http.close()

    def __enter__(self) -> BricksetClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        r = request_with_retry(
            self.http,
            "GET",
            f"{BASE}/{method}",
            params={
                "apiKey": self.api_key,
                "userHash": self.user_hash,
                "params": json.dumps(params),
            },
        )
        data = r.json()
        if data.get("status") == "error":
            raise RuntimeError(f"Brickset {method} error: {data.get('message')}")
        return data

    def get_sets(self, **filters: Any) -> list[dict[str, Any]]:
        """Catalog search. Use `setNumber`, `query` (for barcodes), `theme`, etc."""
        return self._call("getSets", filters).get("sets", [])

    def find_by_barcode(self, barcode: str) -> dict[str, Any] | None:
        sets = self.get_sets(query=barcode)
        return sets[0] if sets else None

    def find_by_set_number(self, set_num: str) -> dict[str, Any] | None:
        sets = self.get_sets(setNumber=set_num)
        return sets[0] if sets else None
