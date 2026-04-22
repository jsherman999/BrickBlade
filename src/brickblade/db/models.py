"""SQLAlchemy models.

`catalog_*` models mirror the Rebrickable CSV downloads. `inventory` and
`prices` are BrickBlade-owned tables for the user's collection.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------- Rebrickable catalog mirror ----------


class Theme(Base):
    __tablename__ = "catalog_themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class Color(Base):
    __tablename__ = "catalog_colors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    rgb: Mapped[str | None] = mapped_column(String(6), nullable=True)
    is_trans: Mapped[bool] = mapped_column(Boolean, default=False)


class PartCategory(Base):
    __tablename__ = "catalog_part_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))


class Part(Base):
    __tablename__ = "catalog_parts"

    part_num: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    part_cat_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_part_categories.id"), nullable=True, index=True
    )
    part_material: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PartRelationship(Base):
    __tablename__ = "catalog_part_relationships"
    __table_args__ = (
        UniqueConstraint("rel_type", "child_part_num", "parent_part_num"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rel_type: Mapped[str] = mapped_column(String(1))
    child_part_num: Mapped[str] = mapped_column(String(64), index=True)
    parent_part_num: Mapped[str] = mapped_column(String(64), index=True)


class Element(Base):
    __tablename__ = "catalog_elements"

    element_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    part_num: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    color_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    design_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Minifig(Base):
    __tablename__ = "catalog_minifigs"

    fig_num: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    num_parts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    img_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class Set(Base):
    __tablename__ = "catalog_sets"

    set_num: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    theme_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    num_parts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    img_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class Inventory(Base):
    __tablename__ = "catalog_inventories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    set_num: Mapped[str] = mapped_column(String(32), index=True)


class InventoryPart(Base):
    __tablename__ = "catalog_inventory_parts"
    __table_args__ = (
        Index("ix_inv_parts_inv", "inventory_id"),
        UniqueConstraint(
            "inventory_id",
            "part_num",
            "color_id",
            "is_spare",
            name="uq_inv_part",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inventory_id: Mapped[int] = mapped_column(Integer)
    part_num: Mapped[str] = mapped_column(String(64))
    color_id: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    is_spare: Mapped[bool] = mapped_column(Boolean, default=False)
    img_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class InventorySet(Base):
    __tablename__ = "catalog_inventory_sets"
    __table_args__ = (UniqueConstraint("inventory_id", "set_num"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inventory_id: Mapped[int] = mapped_column(Integer, index=True)
    set_num: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer)


class InventoryMinifig(Base):
    __tablename__ = "catalog_inventory_minifigs"
    __table_args__ = (UniqueConstraint("inventory_id", "fig_num"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inventory_id: Mapped[int] = mapped_column(Integer, index=True)
    fig_num: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer)


# ---------- BrickBlade tables ----------


class OwnedSet(Base):
    """User's owned sets."""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    set_num: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    condition: Mapped[str] = mapped_column(String(16), default="sealed")  # sealed|used|parted
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PriceSnapshot(Base):
    """Append-only price history, one row per fetch."""

    __tablename__ = "prices"
    __table_args__ = (
        Index("ix_prices_lookup", "set_num", "source", "condition", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    set_num: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))  # brickset|bricklink
    condition: Mapped[str] = mapped_column(String(16))  # sealed|used
    guide_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # sold|stock
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class CatalogImportLog(Base):
    """Tracks Rebrickable CSV imports for idempotency / diagnostics."""

    __tablename__ = "catalog_import_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    csv_name: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
