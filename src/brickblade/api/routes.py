from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from brickblade import __version__
from brickblade.api.deps import get_brickognize, get_clients, get_db, require_bearer
from brickblade.clients.brickognize import BrickognizeClient
from brickblade.core.schemas import LookupResult, OwnedSetOut
from brickblade.core.service import Clients, lookup_set
from brickblade.db.models import OwnedSet, PriceSnapshot

router = APIRouter()


# ---------- Health (unauthenticated) ----------


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# ---------- Lookup ----------


class LookupRequest(BaseModel):
    barcode: str | None = None
    set_num: str | None = None
    force_refresh: bool = False


@router.post(
    "/api/lookup",
    response_model=LookupResult,
    dependencies=[Depends(require_bearer)],
)
def lookup(
    body: LookupRequest,
    db: Session = Depends(get_db),
    clients: Clients = Depends(get_clients),
) -> LookupResult:
    if not body.barcode and not body.set_num:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide `barcode` or `set_num`.",
        )
    result = lookup_set(
        db,
        clients=clients,
        barcode=body.barcode,
        set_num=body.set_num,
        force_refresh=body.force_refresh,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Set not found.")
    return result


# ---------- Image identification ----------


class ImageCandidate(BaseModel):
    id: str
    score: float | None = None
    name: str | None = None
    type: str | None = None


class IdentifyImageResponse(BaseModel):
    candidates: list[ImageCandidate]


@router.post(
    "/api/identify-image",
    response_model=IdentifyImageResponse,
    dependencies=[Depends(require_bearer)],
)
async def identify_image(
    file: UploadFile = File(...),
    bo: BrickognizeClient = Depends(get_brickognize),
) -> IdentifyImageResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload.")
    raw = bo.predict_set(data, filename=file.filename or "upload.jpg")
    return IdentifyImageResponse(
        candidates=[
            ImageCandidate(
                id=str(c.get("id", "")),
                score=c.get("score"),
                name=c.get("name"),
                type=c.get("type"),
            )
            for c in raw
        ]
    )


# ---------- Inventory ----------


class InventoryIn(BaseModel):
    set_num: str
    quantity: int = 1
    condition: str = "sealed"
    notes: str | None = None


@router.get(
    "/api/inventory",
    response_model=list[OwnedSetOut],
    dependencies=[Depends(require_bearer)],
)
def list_inventory(db: Session = Depends(get_db)) -> list[OwnedSetOut]:
    rows = (
        db.execute(select(OwnedSet).order_by(OwnedSet.created_at.desc())).scalars().all()
    )
    return [OwnedSetOut.model_validate(r) for r in rows]


@router.post(
    "/api/inventory",
    response_model=OwnedSetOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_bearer)],
)
def add_inventory(body: InventoryIn, db: Session = Depends(get_db)) -> OwnedSetOut:
    from brickblade.core.sets import normalize_set_num

    row = OwnedSet(
        set_num=normalize_set_num(body.set_num),
        quantity=body.quantity,
        condition=body.condition,
        notes=body.notes,
    )
    db.add(row)
    db.flush()
    return OwnedSetOut.model_validate(row)


@router.delete(
    "/api/inventory/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_bearer)],
)
def remove_inventory(item_id: int, db: Session = Depends(get_db)) -> None:
    result = db.execute(delete(OwnedSet).where(OwnedSet.id == item_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Not found.")


# ---------- Refresh ----------


@router.post(
    "/api/refresh-now",
    dependencies=[Depends(require_bearer)],
)
def refresh_now(
    db: Session = Depends(get_db),
    clients: Clients = Depends(get_clients),
) -> dict[str, int]:
    """Force a price refresh for every owned set. Returns snapshot counts."""
    owned = db.execute(select(OwnedSet.set_num).distinct()).scalars().all()
    before = db.execute(select(PriceSnapshot.id)).scalars().all()
    for set_num in owned:
        lookup_set(db, clients=clients, set_num=set_num, force_refresh=True)
    after = db.execute(select(PriceSnapshot.id)).scalars().all()
    return {"owned_sets": len(owned), "new_snapshots": len(after) - len(before)}
