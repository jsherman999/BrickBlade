"""Barcode → set_num resolution."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from brickblade.clients.brickset import BricksetClient
from brickblade.clients.upcitemdb import UpcItemDbClient
from brickblade.core.sets import normalize_set_num

log = logging.getLogger(__name__)


@dataclass
class BarcodeResolution:
    set_num: str
    source: str  # 'brickset' | 'upcitemdb'


def resolve_barcode(
    barcode: str,
    *,
    brickset: BricksetClient | None,
    upcitemdb: UpcItemDbClient | None,
) -> BarcodeResolution | None:
    """Try Brickset first (it has official EAN/UPC), then UPCitemdb by title regex."""
    if brickset is not None:
        try:
            hit = brickset.find_by_barcode(barcode)
        except Exception as e:  # noqa: BLE001
            log.warning("Brickset barcode lookup failed: %s", e)
            hit = None
        if hit and hit.get("number"):
            return BarcodeResolution(
                set_num=normalize_set_num(str(hit["number"])), source="brickset"
            )

    if upcitemdb is not None:
        try:
            found = upcitemdb.find_lego_set_number(barcode)
        except Exception as e:  # noqa: BLE001
            log.warning("UPCitemdb lookup failed: %s", e)
            found = None
        if found:
            return BarcodeResolution(
                set_num=normalize_set_num(found), source="upcitemdb"
            )

    return None
