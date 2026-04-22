"""Nightly job: refresh prices for every owned set, staggered to be polite."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select

from brickblade.clients.bricklink import BrickLinkClient
from brickblade.clients.brickset import BricksetClient
from brickblade.config import get_settings
from brickblade.core.service import Clients, lookup_set
from brickblade.db.models import OwnedSet, PriceSnapshot
from brickblade.db.session import create_all, session_scope

log = logging.getLogger(__name__)


@dataclass
class RefreshResult:
    owned_sets: int
    snapshots_added: int
    errors: int


def run(*, stagger_seconds: float = 2.0) -> RefreshResult:
    """Refresh prices for every owned set. Designed to be idempotent and safe to rerun."""
    create_all()
    s = get_settings()
    brickset = BricksetClient(s.brickset_key) if s.brickset_key else None
    bricklink = (
        BrickLinkClient(
            s.bl_consumer_key, s.bl_consumer_secret, s.bl_token, s.bl_token_secret
        )
        if s.bl_consumer_key
        else None
    )
    if brickset is None and bricklink is None:
        log.warning("No pricing credentials configured; nothing to do.")
        return RefreshResult(0, 0, 0)

    errors = 0
    snapshots_added = 0
    try:
        with session_scope() as session:
            owned = sorted(
                set(session.execute(select(OwnedSet.set_num)).scalars().all())
            )
            before = session.execute(
                select(PriceSnapshot.id)
            ).scalars().all()
            before_count = len(before)

        clients = Clients(brickset=brickset, bricklink=bricklink, upcitemdb=None)

        for i, set_num in enumerate(owned):
            try:
                with session_scope() as session:
                    lookup_set(
                        session,
                        clients=clients,
                        set_num=set_num,
                        force_refresh=True,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("Refresh failed for %s: %s", set_num, e)
                errors += 1
            if i + 1 < len(owned) and stagger_seconds > 0:
                time.sleep(stagger_seconds)

        with session_scope() as session:
            after = session.execute(select(PriceSnapshot.id)).scalars().all()
            snapshots_added = len(after) - before_count

        return RefreshResult(
            owned_sets=len(owned),
            snapshots_added=snapshots_added,
            errors=errors,
        )
    finally:
        if brickset is not None:
            brickset.close()
        if bricklink is not None:
            bricklink.close()
