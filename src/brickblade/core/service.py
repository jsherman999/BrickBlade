"""High-level orchestrator: barcode/set_num → metadata + prices."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from brickblade.clients.bricklink import BrickLinkClient
from brickblade.clients.brickset import BricksetClient
from brickblade.clients.upcitemdb import UpcItemDbClient
from brickblade.core import lookup, pricing, sets
from brickblade.core.schemas import LookupResult


@dataclass
class Clients:
    brickset: BricksetClient | None = None
    bricklink: BrickLinkClient | None = None
    upcitemdb: UpcItemDbClient | None = None


def lookup_set(
    session: Session,
    *,
    clients: Clients,
    barcode: str | None = None,
    set_num: str | None = None,
    force_refresh: bool = False,
) -> LookupResult | None:
    sources: list[str] = []

    if set_num is None and barcode:
        resolution = lookup.resolve_barcode(
            barcode, brickset=clients.brickset, upcitemdb=clients.upcitemdb
        )
        if resolution is None:
            return None
        set_num = resolution.set_num
        sources.append(resolution.source)

    if set_num is None:
        return None

    canonical = sets.normalize_set_num(set_num)
    metadata = sets.get_metadata(session, canonical)
    if metadata is None:
        return None
    sources.append("rebrickable-mirror")

    prices = pricing.get_or_refresh(
        session,
        canonical,
        brickset=clients.brickset,
        bricklink=clients.bricklink,
        force=force_refresh,
    )
    sources.extend(sorted({p.source for p in prices}))

    return LookupResult(
        set_num=canonical,
        metadata=metadata,
        prices=prices,
        sources=sources,
    )
