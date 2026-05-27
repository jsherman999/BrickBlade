"""Server-rendered web UI mounted at /.

Mirrors every /api/* endpoint so a browser on the Tailnet (e.g. iPhone Safari)
can use the app until the SwiftUI client lands. Auth is a single bearer cookie
set by /login; the existing /api/* routes still accept the same token via the
Authorization header.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
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


def _thumb_url(img_url: str | None) -> str | None:
    """Rebrickable serves 140x140 thumbs at /media/thumbs/sets/<num>.jpg/140x140p.jpg.

    ~5KB instead of ~140KB for the full image. Returns None if input is None
    or doesn't match the expected /media/sets/ pattern (e.g. external URLs).
    """
    if not img_url or "/media/sets/" not in img_url:
        return img_url
    return img_url.replace("/media/sets/", "/media/thumbs/sets/") + "/140x140p.jpg"


def _owned_for(db: Session, set_num: str) -> list[OwnedSet]:
    """Every OwnedSet row for `set_num` (may differ by condition). Used so the
    lookup + detail pages can warn 'you already own this' when shopping."""
    return (
        db.execute(
            select(OwnedSet)
            .where(OwnedSet.set_num == set_num)
            .order_by(OwnedSet.created_at.desc())
        )
        .scalars()
        .all()
    )


_SORTS = {
    "added": OwnedSet.created_at.desc(),
    "set": OwnedSet.set_num.asc(),
    "qty": OwnedSet.quantity.desc(),
    "name": Set.name.asc(),
    "theme": Theme.name.asc(),
    # "value" is computed in Python after the price lookup; handled separately.
}


def _build_inventory_rows(
    db: Session, sort: str, theme_filter: str
) -> tuple[list[dict], float, list[tuple[str, dict]], list[str]]:
    """Shared between /  and /export.csv. Returns (rows, total, theme_summary, all_themes)."""
    q = (
        select(OwnedSet, Set.name, Set.img_url, Set.year, Set.num_parts, Theme.name)
        .join(Set, Set.set_num == OwnedSet.set_num, isouter=True)
        .join(Theme, Theme.id == Set.theme_id, isouter=True)
    )
    if theme_filter:
        q = q.where(Theme.name == theme_filter)
    if sort in _SORTS:
        q = q.order_by(_SORTS[sort])
    else:
        q = q.order_by(_SORTS["added"])

    rows: list[dict] = []
    total = 0.0
    for owned, set_name, img_url, year, num_parts, theme_name in db.execute(q).all():
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
                "year": year,
                "num_parts": num_parts,
                "img_url": img_url,
                "thumb_url": _thumb_url(img_url),
                "quantity": owned.quantity,
                "condition": owned.condition,
                "price": price,
                "line": line,
                "created_at": owned.created_at,
            }
        )

    if sort == "value":
        rows.sort(key=lambda r: r["line"], reverse=True)

    # Per-theme totals from the (already filtered) rows so the breakdown
    # reflects whatever the user is currently looking at.
    theme_totals: dict[str, dict] = {}
    for r in rows:
        t = r["theme_name"] or "(unknown)"
        bucket = theme_totals.setdefault(t, {"count": 0, "total": 0.0})
        bucket["count"] += r["quantity"]
        bucket["total"] += r["line"]
    theme_summary = sorted(
        theme_totals.items(), key=lambda kv: kv[1]["total"], reverse=True
    )

    # Full theme universe (ignoring current filter) for the dropdown.
    all_themes = [
        t
        for (t,) in db.execute(
            select(Theme.name)
            .join(Set, Set.theme_id == Theme.id)
            .join(OwnedSet, OwnedSet.set_num == Set.set_num)
            .distinct()
            .order_by(Theme.name)
        ).all()
        if t
    ]

    return rows, total, theme_summary, all_themes


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
    sort = request.query_params.get("sort", "added")
    theme_filter = request.query_params.get("theme", "")
    rows, total, theme_summary, all_themes = _build_inventory_rows(db, sort, theme_filter)
    return TEMPLATES.TemplateResponse(
        request,
        "inventory.html",
        {
            "rows": rows,
            "total": total,
            "theme_summary": theme_summary,
            "all_themes": all_themes,
            "sort": sort,
            "theme_filter": theme_filter,
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err"),
        },
    )


@router.get("/export.csv", dependencies=[Depends(require_web_session)])
def export_csv(request: Request, db: Session = Depends(get_db)):
    sort = request.query_params.get("sort", "added")
    theme_filter = request.query_params.get("theme", "")
    rows, total, _, _ = _build_inventory_rows(db, sort, theme_filter)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "set_num", "name", "theme", "year", "num_parts",
            "quantity", "condition", "latest_avg_price", "line_total",
            "added_at",
        ]
    )
    for r in rows:
        w.writerow(
            [
                r["set_num"], r["name"] or "", r["theme_name"] or "",
                r["year"] or "", r["num_parts"] or "",
                r["quantity"], r["condition"],
                f"{r['price']:.2f}" if r["price"] is not None else "",
                f"{r['line']:.2f}",
                r["created_at"].isoformat() if r["created_at"] else "",
            ]
        )
    w.writerow([])
    w.writerow(["TOTAL", "", "", "", "", "", "", "", f"{total:.2f}", ""])
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="brickblade-inventory.csv"'
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
    return TEMPLATES.TemplateResponse(
        request,
        "lookup_result.html",
        {"result": result, "owned": _owned_for(db, result.set_num)},
    )


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
        request,
        "set_detail.html",
        {"meta": meta, "snapshots": snapshots, "owned": _owned_for(db, canonical)},
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
