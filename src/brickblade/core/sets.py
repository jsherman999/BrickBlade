"""Canonical set-number handling and metadata fetch from the local catalog."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from brickblade.core.schemas import SetMetadata
from brickblade.db.models import Set, Theme

_DIGITS_ONLY = re.compile(r"^\d+$")


def normalize_set_num(value: str) -> str:
    """Rebrickable/BrickLink require `{number}-{variant}`. Default variant is 1.

    Accepts '10294', '10294-1', '  10294 '. Passes non-numeric tokens through
    unchanged so things like 'fig-012345' or MOCs are not mangled.
    """
    v = value.strip()
    if "-" in v:
        return v
    if _DIGITS_ONLY.match(v):
        return f"{v}-1"
    return v


def get_metadata(session: Session, set_num: str) -> SetMetadata | None:
    row = session.execute(
        select(Set, Theme.name)
        .join(Theme, Theme.id == Set.theme_id, isouter=True)
        .where(Set.set_num == set_num)
    ).first()
    if row is None:
        return None
    s, theme_name = row
    return SetMetadata(
        set_num=s.set_num,
        name=s.name,
        year=s.year,
        theme_id=s.theme_id,
        theme_name=theme_name,
        num_parts=s.num_parts,
        img_url=s.img_url,
    )
