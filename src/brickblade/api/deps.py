"""Dependency providers for the FastAPI app.

All upstream clients are constructed once per request and injected so tests
can override them cleanly with FastAPI's `app.dependency_overrides`.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from brickblade.clients.bricklink import BrickLinkClient
from brickblade.clients.brickognize import BrickognizeClient
from brickblade.clients.brickset import BricksetClient
from brickblade.clients.upcitemdb import UpcItemDbClient
from brickblade.config import Settings, get_settings
from brickblade.core.service import Clients
from brickblade.db.session import session_scope


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def require_bearer(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = f"Bearer {settings.brickblade_bearer_token}"
    if not authorization or authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )


def get_clients(settings: Settings = Depends(get_settings)) -> Iterator[Clients]:
    brickset = (
        BricksetClient(settings.brickset_key, settings.brickset_username)
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
