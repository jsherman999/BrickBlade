"""Tests for core lookup / pricing / service using fake clients."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import insert

from brickblade.core import lookup as lookup_mod
from brickblade.core import pricing, sets
from brickblade.core.service import Clients, lookup_set
from brickblade.db import models
from brickblade.db.session import create_all, session_scope


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BRICKBLADE_DB_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("BRICKBLADE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRICKBLADE_PRICE_TTL_HOURS", "48")
    from brickblade import config as cfg
    from brickblade.db import session as db_session

    cfg.get_settings.cache_clear()
    db_session._engine = None
    db_session._SessionLocal = None
    create_all()

    # Seed one theme + one set so metadata lookup works.
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
    yield


class FakeBrickset:
    def __init__(self, by_barcode=None, by_setnum=None) -> None:
        self._by_barcode = by_barcode or {}
        self._by_setnum = by_setnum or {}

    def find_by_barcode(self, b: str) -> dict[str, Any] | None:
        return self._by_barcode.get(b)

    def find_by_set_number(self, sn: str) -> dict[str, Any] | None:
        return self._by_setnum.get(sn)


class FakeBrickLink:
    def __init__(self, price: dict[str, Any]) -> None:
        self._price = price
        self.calls = 0

    def get_price_guide(self, *a, **kw) -> dict[str, Any]:
        self.calls += 1
        return self._price


class FakeUpc:
    def __init__(self, result: str | None) -> None:
        self._r = result

    def find_lego_set_number(self, upc: str) -> str | None:
        return self._r


def test_normalize_set_num():
    assert sets.normalize_set_num("10294") == "10294-1"
    assert sets.normalize_set_num("10294-2") == "10294-2"
    assert sets.normalize_set_num("  75192  ") == "75192-1"


def test_resolve_barcode_brickset_wins():
    bs = FakeBrickset(by_barcode={"5702014264335": {"number": "10294"}})
    upc = FakeUpc(result="99999")
    r = lookup_mod.resolve_barcode("5702014264335", brickset=bs, upcitemdb=upc)
    assert r is not None
    assert r.set_num == "10294-1"
    assert r.source == "brickset"


def test_resolve_barcode_falls_back_to_upcitemdb():
    bs = FakeBrickset(by_barcode={})
    upc = FakeUpc(result="10294")
    r = lookup_mod.resolve_barcode("000", brickset=bs, upcitemdb=upc)
    assert r is not None and r.source == "upcitemdb" and r.set_num == "10294-1"


def test_resolve_barcode_none_found():
    assert (
        lookup_mod.resolve_barcode(
            "x", brickset=FakeBrickset(), upcitemdb=FakeUpc(None)
        )
        is None
    )


def test_pricing_uses_cache_when_fresh(fresh_db):
    bl = FakeBrickLink({"avg_price": "500.0", "unit_quantity": 3})
    with session_scope() as s:
        # First call: writes a snapshot.
        pricing.get_or_refresh(
            s, "10294-1", brickset=None, bricklink=bl, ttl_hours=48
        )
    with session_scope() as s:
        # Second call: fresh cache, no upstream hit.
        out = pricing.get_or_refresh(
            s, "10294-1", brickset=None, bricklink=bl, ttl_hours=48
        )
    assert bl.calls == 1
    assert out[0].avg_price == 500.0
    assert out[0].source == "bricklink"


def test_pricing_refreshes_when_stale(fresh_db):
    bl = FakeBrickLink({"avg_price": "500.0"})
    with session_scope() as s:
        stale = datetime.now(UTC) - timedelta(hours=100)
        s.add(
            models.PriceSnapshot(
                set_num="10294-1",
                source="bricklink",
                condition="used",
                guide_type="sold",
                currency="USD",
                avg_price=100.0,
                fetched_at=stale,
            )
        )
    with session_scope() as s:
        out = pricing.get_or_refresh(
            s, "10294-1", brickset=None, bricklink=bl, ttl_hours=48
        )
    assert bl.calls == 1
    assert out[0].avg_price == 500.0


def test_pricing_force_bypasses_cache(fresh_db):
    bl = FakeBrickLink({"avg_price": "42.0"})
    with session_scope() as s:
        pricing.get_or_refresh(s, "10294-1", brickset=None, bricklink=bl)
    with session_scope() as s:
        pricing.get_or_refresh(s, "10294-1", brickset=None, bricklink=bl, force=True)
    assert bl.calls == 2


def test_lookup_set_by_setnum(fresh_db):
    bl = FakeBrickLink({"avg_price": "500.0"})
    with session_scope() as s:
        res = lookup_set(
            s,
            clients=Clients(brickset=None, bricklink=bl, upcitemdb=None),
            set_num="10294",
        )
    assert res is not None
    assert res.set_num == "10294-1"
    assert res.metadata.name == "Titanic"
    assert res.metadata.theme_name == "Creator Expert"
    assert any(p.source == "bricklink" for p in res.prices)
    assert "rebrickable-mirror" in res.sources


def test_lookup_set_by_barcode_unknown_set(fresh_db):
    with session_scope() as s:
        res = lookup_set(
            s,
            clients=Clients(
                brickset=FakeBrickset(by_barcode={"b": {"number": "99999"}}),
                upcitemdb=None,
                bricklink=None,
            ),
            barcode="b",
        )
    # Barcode resolves, but metadata not in local mirror → None.
    assert res is None


def test_lookup_set_set_not_in_catalog(fresh_db):
    with session_scope() as s:
        res = lookup_set(
            s,
            clients=Clients(brickset=None, bricklink=None, upcitemdb=None),
            set_num="99999",
        )
    assert res is None
