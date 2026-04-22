"""Response schemas shared by the API layer and CLI."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PriceSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    condition: str
    guide_type: str | None
    currency: str
    avg_price: float | None
    min_price: float | None
    max_price: float | None
    qty: int | None
    fetched_at: datetime


class SetMetadata(BaseModel):
    set_num: str
    name: str
    year: int | None
    theme_id: int | None
    theme_name: str | None
    num_parts: int | None
    img_url: str | None


class LookupResult(BaseModel):
    set_num: str
    metadata: SetMetadata
    prices: list[PriceSnapshotOut]
    sources: list[str]


class OwnedSetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    set_num: str
    quantity: int
    condition: str
    notes: str | None
    created_at: datetime
