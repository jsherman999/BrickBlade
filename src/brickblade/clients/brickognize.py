"""Brickognize — image recognition for LEGO sets, minifigs, and parts."""

from __future__ import annotations

from typing import Any

import httpx

from brickblade.clients.base import request_with_retry

BASE = "https://api.brickognize.com"


class BrickognizeClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns = client is None
        self.http = client or httpx.Client(timeout=60.0)

    def close(self) -> None:
        if self._owns:
            self.http.close()

    def __enter__(self) -> BrickognizeClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def predict_set(self, image_bytes: bytes, filename: str = "upload.jpg") -> list[dict[str, Any]]:
        r = request_with_retry(
            self.http,
            "POST",
            f"{BASE}/predict/",
            files={"query_image": (filename, image_bytes, "image/jpeg")},
        )
        return r.json().get("items", [])
