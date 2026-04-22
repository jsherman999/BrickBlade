"""Pricing: cache-first reads of the append-only `prices` table; refresh via upstream."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from brickblade.clients.bricklink import BrickLinkClient
from brickblade.clients.brickset import BricksetClient
from brickblade.config import get_settings
from brickblade.core.schemas import PriceSnapshotOut
from brickblade.db.models import PriceSnapshot

log = logging.getLogger(__name__)


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _latest(session: Session, set_num: str, source: str, condition: str) -> PriceSnapshot | None:
    q = (
        select(PriceSnapshot)
        .where(
            PriceSnapshot.set_num == set_num,
            PriceSnapshot.source == source,
            PriceSnapshot.condition == condition,
        )
        .order_by(PriceSnapshot.fetched_at.desc())
        .limit(1)
    )
    return session.execute(q).scalar_one_or_none()


def _is_fresh(snap: PriceSnapshot | None, ttl_hours: int) -> bool:
    if snap is None or snap.fetched_at is None:
        return False
    age = datetime.now(timezone.utc) - snap.fetched_at.replace(tzinfo=timezone.utc)
    return age < timedelta(hours=ttl_hours)


def fetch_brickset_sealed(
    session: Session, set_num: str, brickset: BricksetClient
) -> PriceSnapshot:
    data = brickset.find_by_set_number(set_num) or {}
    us = (data.get("LEGOCom") or {}).get("US") or {}
    avg = _to_float(us.get("retailPrice")) or _to_float(
        (data.get("collections") or {}).get("averageSellingPrice")
    )
    snap = PriceSnapshot(
        set_num=set_num,
        source="brickset",
        condition="sealed",
        guide_type=None,
        currency="USD",
        avg_price=avg,
        min_price=None,
        max_price=None,
        qty=None,
        raw=json.dumps(data)[:50_000],
    )
    session.add(snap)
    session.flush()
    return snap


def fetch_bricklink_used(
    session: Session,
    set_num: str,
    bricklink: BrickLinkClient,
    *,
    guide_type: str = "sold",
) -> PriceSnapshot:
    data = bricklink.get_price_guide(
        "SET", set_num, new_or_used="U", guide_type=guide_type  # type: ignore[arg-type]
    )
    snap = PriceSnapshot(
        set_num=set_num,
        source="bricklink",
        condition="used",
        guide_type=guide_type,
        currency=data.get("currency_code", "USD"),
        avg_price=_to_float(data.get("avg_price")),
        min_price=_to_float(data.get("min_price")),
        max_price=_to_float(data.get("max_price")),
        qty=data.get("unit_quantity") or data.get("total_quantity"),
        raw=json.dumps(data)[:50_000],
    )
    session.add(snap)
    session.flush()
    return snap


def get_or_refresh(
    session: Session,
    set_num: str,
    *,
    brickset: BricksetClient | None,
    bricklink: BrickLinkClient | None,
    ttl_hours: int | None = None,
    force: bool = False,
) -> list[PriceSnapshotOut]:
    """Return cached snapshots if fresh; otherwise fetch each source and append."""
    ttl = ttl_hours if ttl_hours is not None else get_settings().brickblade_price_ttl_hours
    out: list[PriceSnapshot] = []

    if brickset is not None:
        snap = _latest(session, set_num, "brickset", "sealed")
        if force or not _is_fresh(snap, ttl):
            try:
                snap = fetch_brickset_sealed(session, set_num, brickset)
            except Exception as e:  # noqa: BLE001
                log.warning("Brickset price fetch failed for %s: %s", set_num, e)
        if snap is not None:
            out.append(snap)

    if bricklink is not None:
        snap = _latest(session, set_num, "bricklink", "used")
        if force or not _is_fresh(snap, ttl):
            try:
                snap = fetch_bricklink_used(session, set_num, bricklink)
            except Exception as e:  # noqa: BLE001
                log.warning("BrickLink price fetch failed for %s: %s", set_num, e)
        if snap is not None:
            out.append(snap)

    return [PriceSnapshotOut.model_validate(s) for s in out]
