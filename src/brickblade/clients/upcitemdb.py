"""UPCitemdb free 'Explorer' tier: 100 lookups/day, no signup."""

from __future__ import annotations

import re
from typing import Any

import httpx

from brickblade.clients.base import NotFound, request_with_retry

BASE = "https://api.upcitemdb.com/prod/trial"

_SET_NUMBER_RE = re.compile(r"\b(\d{4,6})\b")


class UpcItemDbClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns = client is None
        self.http = client or httpx.Client(timeout=15.0)

    def close(self) -> None:
        if self._owns:
            self.http.close()

    def __enter__(self) -> UpcItemDbClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def lookup(self, upc: str) -> dict[str, Any]:
        try:
            r = request_with_retry(self.http, "GET", f"{BASE}/lookup", params={"upc": upc})
        except NotFound:
            return {"items": []}
        return r.json()

    def find_lego_set_number(self, upc: str) -> str | None:
        """Try to pull a LEGO set number out of the product title."""
        data = self.lookup(upc)
        for item in data.get("items", []):
            title = item.get("title", "")
            if "lego" not in title.lower():
                continue
            m = _SET_NUMBER_RE.search(title)
            if m:
                return m.group(1)
        return None
