"""Dependency providers for the FastAPI app.

All upstream clients are constructed once per request and injected so tests
can override them cleanly with FastAPI's `app.dependency_overrides`.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from brickblade.clients.bricklink import BrickLinkClient
from brickblade.clients.brickognize import BrickognizeClient
from brickblade.clients.brickset import BricksetClient
from brickblade.clients.upcitemdb import UpcItemDbClient
from brickblade.config import Settings, get_settings
from brickblade.core.service import Clients
from brickblade.db.session import session_scope

SESSION_COOKIE = "bb_session"


class AuthRedirect(Exception):
    """Raised by web-route auth dep when the session cookie is missing/invalid.

    The app exception handler converts this into a 303 to /login. Using an
    exception (not a return value) lets us guard whole routes with Depends().
    """


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def require_bearer(
    request: Request,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Accept either Authorization: Bearer <token> or the bb_session cookie.

    Header path is the canonical API auth (curl, SwiftUI). Cookie path lets the
    server-rendered web UI use the same routes after /login sets the cookie.
    """
    token = settings.brickblade_bearer_token
    header_ok = authorization == f"Bearer {token}"
    cookie_ok = request.cookies.get(SESSION_COOKIE) == token
    if not (header_ok or cookie_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing credentials",
        )


def require_web_session(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Web-route guard: cookie only, redirects to /login on failure."""
    if request.cookies.get(SESSION_COOKIE) != settings.brickblade_bearer_token:
        raise AuthRedirect()


def get_clients(settings: Settings = Depends(get_settings)) -> Iterator[Clients]:
    brickset = (
        BricksetClient(settings.brickset_key)
        if settings.brickset_key
        else None
    )
    bricklink = (
        BrickLinkClient(
            settings.bl_consumer_key,
            settings.bl_consumer_secret,
            settings.bl_token,
            settings.bl_token_secret,
        )
        if settings.bl_consumer_key
        else None
    )
    upc = UpcItemDbClient()
    try:
        yield Clients(brickset=brickset, bricklink=bricklink, upcitemdb=upc)
    finally:
        if brickset is not None:
            brickset.close()
        if bricklink is not None:
            bricklink.close()
        upc.close()


def get_brickognize() -> Iterator[BrickognizeClient]:
    c = BrickognizeClient()
    try:
        yield c
    finally:
        c.close()
