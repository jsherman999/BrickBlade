"""Server-rendered web UI mounted at /.

Mirrors every /api/* endpoint so a browser on the Tailnet (e.g. iPhone Safari)
can use the app until the SwiftUI client lands. Auth is a single bearer cookie
set by /login; the existing /api/* routes still accept the same token via the
Authorization header.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from brickblade.api.deps import (
    SESSION_COOKIE,
    get_brickognize,
    get_clients,
    get_db,
    require_web_session,
)
from brickblade.clients.brickognize import BrickognizeClient
from brickblade.config import Settings, get_settings
from brickblade.core.service import Clients, lookup_set
from brickblade.core.sets import get_metadata, normalize_set_num
from brickblade.db.models import OwnedSet, PriceSnapshot, Set, Theme

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


def _set_session_cookie(token: str, target: str = "/") -> RedirectResponse:
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
    )
    return resp


# ---------- auth ----------


@router.get("/login")
def login_form(request: Request):
    err = "Invalid token." if request.query_params.get("error") == "bad" else None
    return TEMPLATES.TemplateResponse(request, "login.html", {"err": err})


@router.post("/login")
def login_submit(
    token: Annotated[str, Form()],
    settings: Settings = Depends(get_settings),
):
    if token != settings.brickblade_bearer_token:
        return RedirectResponse("/login?error=bad", status_code=303)
    return _set_session_cookie(token)


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------- inventory list (home) ----------


@router.get("/", dependencies=[Depends(require_web_session)])
def home(request: Request, db: Session = Depends(get_db)):
    q = (
        select(OwnedSet, Set.name, Theme.name)
        .join(Set, Set.set_num == OwnedSet.set_num, isouter=True)
        .join(Theme, Theme.id == Set.theme_id, isouter=True)
        .order_by(OwnedSet.created_at.desc())
    )
    rows: list[dict] = []
    total = 0.0
    for owned, set_name, theme_name in db.execute(q).all():
        snap = db.execute(
            select(PriceSnapshot)
            .where(PriceSnapshot.set_num == owned.set_num)
            .where(PriceSnapshot.avg_price.isnot(None))
            .order_by(PriceSnapshot.fetched_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        price = float(snap.avg_price) if snap and snap.avg_price else None
        line = (price or 0.0) * owned.quantity
        total += line
        rows.append(
            {
                "id": owned.id,
                "set_num": owned.set_num,
                "name": set_name,
                "theme_name": theme_name,
                "quantity": owned.quantity,
                "condition": owned.condition,
                "price": price,
                "line": line,
            }
        )
    return TEMPLATES.TemplateResponse(
        request,
        "inventory.html",
        {
            "rows": rows,
            "total": total,
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err"),
        },
    )


# ---------- lookup ----------


@router.get("/lookup", dependencies=[Depends(require_web_session)])
def lookup_form(request: Request):
    return TEMPLATES.TemplateResponse(request, "lookup.html", {})


@router.post("/lookup", dependencies=[Depends(require_web_session)])
def lookup_submit(
    request: Request,
    query: Annotated[str, Form()],
    force_refresh: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    clients: Clients = Depends(get_clients),
):
    q = query.strip()
    is_barcode = q.isdigit() and len(q) >= 11
    result = lookup_set(
        db,
        clients=clients,
        barcode=q if is_barcode else None,
        set_num=None if is_barcode else q,
        force_refresh=bool(force_refresh),
    )
    if result is None:
        return TEMPLATES.TemplateResponse(
            request,
            "lookup.html",
            {"err": f"Nothing found for '{q}'."},
            status_code=404,
        )
    return TEMPLATES.TemplateResponse(request, "lookup_result.html", {"result": result})


# ---------- identify ----------


@router.get("/identify", dependencies=[Depends(require_web_session)])
def identify_form(request: Request):
    return TEMPLATES.TemplateResponse(request, "identify.html", {})


@router.post("/identify", dependencies=[Depends(require_web_session)])
async def identify_submit(
    request: Request,
    file: UploadFile = File(...),
    bo: BrickognizeClient = Depends(get_brickognize),
):
    data = await file.read()
    if not data:
        return TEMPLATES.TemplateResponse(
            request,
            "identify.html",
            {"err": "Empty upload."},
            status_code=400,
        )
    raw = bo.predict_set(data, filename=file.filename or "upload.jpg")
    return TEMPLATES.TemplateResponse(
        request, "identify_result.html", {"candidates": raw}
    )


# ---------- set detail ----------


@router.get("/set/{set_num}", dependencies=[Depends(require_web_session)])
def set_detail(request: Request, set_num: str, db: Session = Depends(get_db)):
    canonical = normalize_set_num(set_num)
    meta = get_metadata(db, canonical)
    if meta is None:
        return TEMPLATES.TemplateResponse(
            request,
            "lookup.html",
            {"err": f"Unknown set '{canonical}'."},
            status_code=404,
        )
    snapshots = (
        db.execute(
            select(PriceSnapshot)
            .where(PriceSnapshot.set_num == canonical)
            .order_by(PriceSnapshot.fetched_at.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    return TEMPLATES.TemplateResponse(
        request, "set_detail.html", {"meta": meta, "snapshots": snapshots}
    )


# ---------- inventory mutations ----------


@router.post("/set/{set_num}/add", dependencies=[Depends(require_web_session)])
def add_to_inventory(
    set_num: str,
    quantity: Annotated[int, Form()] = 1,
    condition: Annotated[str, Form()] = "sealed",
    db: Session = Depends(get_db),
):
    canonical = normalize_set_num(set_num)
    db.add(OwnedSet(set_num=canonical, quantity=quantity, condition=condition))
    return RedirectResponse(f"/?msg=Added+{canonical}", status_code=303)


@router.post("/inventory/{item_id}/delete", dependencies=[Depends(require_web_session)])
def delete_from_inventory(item_id: int, db: Session = Depends(get_db)):
    db.execute(sa_delete(OwnedSet).where(OwnedSet.id == item_id))
    return RedirectResponse("/?msg=Removed", status_code=303)


@router.post("/refresh", dependencies=[Depends(require_web_session)])
def refresh_prices(
    db: Session = Depends(get_db),
    clients: Clients = Depends(get_clients),
):
    owned = db.execute(select(OwnedSet.set_num).distinct()).scalars().all()
    for sn in owned:
        lookup_set(db, clients=clients, set_num=sn, force_refresh=True)
    return RedirectResponse(f"/?msg=Refreshed+{len(owned)}+sets", status_code=303)
