"""Client tests using httpx MockTransport — no real network."""

from __future__ import annotations

import json

import httpx
import pytest

from brickblade.clients.base import ClientError, NotFound
from brickblade.clients.bricklink import BrickLinkClient
from brickblade.clients.brickognize import BrickognizeClient
from brickblade.clients.brickset import BricksetClient
from brickblade.clients.rebrickable import RebrickableClient
from brickblade.clients.upcitemdb import UpcItemDbClient


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def test_rebrickable_get_set():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "key TESTKEY"
        assert req.url.path == "/api/v3/lego/sets/10294-1/"
        return httpx.Response(200, json={"set_num": "10294-1", "name": "Titanic"})

    with RebrickableClient("TESTKEY", client=_client(handler)) as rb:
        data = rb.get_set("10294-1")
        assert data["name"] == "Titanic"


def test_rebrickable_404_raises_notfound():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not found."})

    with RebrickableClient("k", client=_client(handler)) as rb, pytest.raises(NotFound):
        rb.get_set("nope")


def test_brickset_getsets_query_as_json_string():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v3.asmx/getSets"
        params = dict(req.url.params)
        assert params["apiKey"] == "BS"
        assert json.loads(params["params"]) == {"query": "5702014264335"}
        return httpx.Response(
            200,
            json={
                "status": "success",
                "sets": [{"number": "10294", "name": "Titanic"}],
            },
        )

    with BricksetClient("BS", client=_client(handler)) as bs:
        hit = bs.find_by_barcode("5702014264335")
        assert hit and hit["number"] == "10294"


def test_brickset_error_status_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "message": "bad key"})

    with BricksetClient("BS", client=_client(handler)) as bs, pytest.raises(RuntimeError):
        bs.find_by_set_number("10294-1")


def test_bricklink_oauth_signs_request():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization", "")
        assert req.url.path == "/api/store/v1/items/SET/10294-1/price"
        assert dict(req.url.params) == {
            "new_or_used": "U",
            "guide_type": "sold",
            "currency_code": "USD",
        }
        return httpx.Response(
            200,
            json={
                "meta": {"code": 200},
                "data": {"avg_price": "500.00", "unit_quantity": 3},
            },
        )

    with BrickLinkClient("ck", "cs", "tk", "ts", client=_client(handler)) as bl:
        data = bl.get_price_guide("SET", "10294-1")
        assert data["avg_price"] == "500.00"
    assert seen["auth"].startswith("OAuth ")
    assert 'oauth_signature=' in seen["auth"]


def test_upcitemdb_extracts_set_number():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"title": "LEGO 10294 Titanic Creator Expert"}]},
        )

    with UpcItemDbClient(client=_client(handler)) as u:
        assert u.find_lego_set_number("5702014264335") == "10294"


def test_upcitemdb_ignores_non_lego():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"items": [{"title": "Random Product 12345"}]}
        )

    with UpcItemDbClient(client=_client(handler)) as u:
        assert u.find_lego_set_number("000000000000") is None


def test_brickognize_predict():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/predict/"
        assert b"query_image" in req.content
        return httpx.Response(
            200, json={"items": [{"id": "10294-1", "score": 0.92}]}
        )

    with BrickognizeClient(client=_client(handler)) as bo:
        hits = bo.predict_set(b"\xff\xd8fakejpeg")
        assert hits[0]["id"] == "10294-1"


def test_retry_on_5xx_then_success():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"set_num": "x", "name": "ok"})

    with RebrickableClient("k", client=_client(handler)) as rb:
        data = rb.get_set("x")
        assert data["name"] == "ok"
    assert calls["n"] == 2


def test_4xx_other_than_404_raises_client_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    with RebrickableClient("k", client=_client(handler)) as rb, pytest.raises(ClientError):
        rb.get_set("x")
