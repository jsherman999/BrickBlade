"""API integration tests with overridden deps — no real upstream calls."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from brickblade.api import deps
from brickblade.api.app import create_app
from brickblade.core.service import Clients
from brickblade.db import models
from brickblade.db.session import create_all, session_scope

BEARER = "Bearer test-token"


class FakeBL:
    def get_price_guide(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return {"avg_price": "500.0", "unit_quantity": 3}


class FakeBS:
    def find_by_set_number(self, sn: str) -> dict[str, Any] | None:
        return {"LEGOCom": {"US": {"retailPrice": 629.99}}}

    def find_by_barcode(self, b: str) -> dict[str, Any] | None:
        return {"number": "10294"} if b == "5702014264335" else None


class FakeBO:
    def predict_set(self, data: bytes, filename: str = "x.jpg") -> list[dict[str, Any]]:
        return [{"id": "10294-1", "score": 0.95, "name": "Titanic", "type": "set"}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BRICKBLADE_DB_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("BRICKBLADE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRICKBLADE_BEARER_TOKEN", "test-token")
    from brickblade import config as cfg
    from brickblade.db import session as db_session

    cfg.get_settings.cache_clear()
    db_session._engine = None
    db_session._SessionLocal = None
    create_all()
    with session_scope() as s:
        s.execute(insert(models.Theme), [{"id": 1, "name": "Creator Expert"}])
        s.execute(
            insert(models.Set),
            [
                {
                    "set_num": "10294-1",
                    "name": "Titanic",
                    "year": 2021,
                    "theme_id": 1,
                    "num_parts": 9090,
                    "img_url": "https://ex/titanic.jpg",
                }
            ],
        )

    app = create_app()

    def _clients():
        yield Clients(brickset=FakeBS(), bricklink=FakeBL(), upcitemdb=None)

    def _bo():
        yield FakeBO()

    app.dependency_overrides[deps.get_clients] = _clients
    app.dependency_overrides[deps.get_brickognize] = _bo

    return TestClient(app)


def test_health_is_public(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_lookup_requires_auth(client: TestClient):
    r = client.post("/api/lookup", json={"set_num": "10294"})
    assert r.status_code == 401


def test_lookup_by_set_num(client: TestClient):
    r = client.post(
        "/api/lookup",
        json={"set_num": "10294"},
        headers={"Authorization": BEARER},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["set_num"] == "10294-1"
    assert body["metadata"]["name"] == "Titanic"
    sources = {p["source"] for p in body["prices"]}
    assert sources == {"brickset", "bricklink"}


def test_lookup_by_barcode(client: TestClient):
    r = client.post(
        "/api/lookup",
        json={"barcode": "5702014264335"},
        headers={"Authorization": BEARER},
    )
    assert r.status_code == 200
    assert r.json()["set_num"] == "10294-1"


def test_lookup_requires_a_param(client: TestClient):
    r = client.post("/api/lookup", json={}, headers={"Authorization": BEARER})
    assert r.status_code == 400


def test_lookup_not_found(client: TestClient):
    r = client.post(
        "/api/lookup", json={"set_num": "99999"}, headers={"Authorization": BEARER}
    )
    assert r.status_code == 404


def test_inventory_crud_flow(client: TestClient):
    r = client.post(
        "/api/inventory",
        json={"set_num": "10294", "quantity": 1, "condition": "sealed"},
        headers={"Authorization": BEARER},
    )
    assert r.status_code == 201
    item_id = r.json()["id"]
    assert r.json()["set_num"] == "10294-1"  # normalized

    r = client.get("/api/inventory", headers={"Authorization": BEARER})
    assert r.status_code == 200
    assert any(x["id"] == item_id for x in r.json())

    r = client.delete(f"/api/inventory/{item_id}", headers={"Authorization": BEARER})
    assert r.status_code == 204

    r = client.delete(f"/api/inventory/{item_id}", headers={"Authorization": BEARER})
    assert r.status_code == 404


def test_identify_image(client: TestClient):
    r = client.post(
        "/api/identify-image",
        files={"file": ("box.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")},
        headers={"Authorization": BEARER},
    )
    assert r.status_code == 200
    cands = r.json()["candidates"]
    assert cands[0]["id"] == "10294-1"
    assert cands[0]["score"] == 0.95


def test_refresh_now(client: TestClient):
    client.post(
        "/api/inventory",
        json={"set_num": "10294"},
        headers={"Authorization": BEARER},
    )
    r = client.post("/api/refresh-now", headers={"Authorization": BEARER})
    assert r.status_code == 200
    assert r.json()["owned_sets"] == 1
    assert r.json()["new_snapshots"] >= 1
