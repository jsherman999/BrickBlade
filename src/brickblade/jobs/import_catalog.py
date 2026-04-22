"""Download and import Rebrickable CSV dumps into the local mirror.

Rebrickable publishes daily gzipped CSVs at stable CDN URLs. We download
each file, hash it, skip if unchanged since the last import, and UPSERT
rows into the `catalog_*` tables inside a single transaction per file.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from brickblade.config import get_settings
from brickblade.db.models import (
    CatalogImportLog,
    Color,
    Element,
    Inventory,
    InventoryMinifig,
    InventoryPart,
    InventorySet,
    Minifig,
    Part,
    PartCategory,
    PartRelationship,
    Set,
    Theme,
)
from brickblade.db.session import create_all, session_scope

log = logging.getLogger(__name__)

REBRICKABLE_CDN = "https://cdn.rebrickable.com/media/downloads"


def _to_int(v: str) -> int | None:
    v = v.strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _to_bool(v: str) -> bool:
    return v.strip().lower() in {"t", "true", "1", "yes"}


def _nz(v: str) -> str | None:
    v = v.strip()
    return v or None


@dataclass(frozen=True)
class CsvSpec:
    name: str  # filename stem, e.g. "sets"
    model: Any
    columns: list[str]  # CSV column order (from Rebrickable header)
    transform: Any  # callable(row_dict) -> dict for model insert


# ---------- Per-CSV transforms ----------


def _t_themes(r: dict[str, str]) -> dict[str, Any]:
    return {"id": int(r["id"]), "name": r["name"], "parent_id": _to_int(r["parent_id"])}


def _t_colors(r: dict[str, str]) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "name": r["name"],
        "rgb": _nz(r["rgb"]),
        "is_trans": _to_bool(r["is_trans"]),
    }


def _t_part_categories(r: dict[str, str]) -> dict[str, Any]:
    return {"id": int(r["id"]), "name": r["name"]}


def _t_parts(r: dict[str, str]) -> dict[str, Any]:
    return {
        "part_num": r["part_num"],
        "name": r["name"],
        "part_cat_id": _to_int(r["part_cat_id"]),
        "part_material": _nz(r.get("part_material", "")),
    }


def _t_part_relationships(r: dict[str, str]) -> dict[str, Any]:
    return {
        "rel_type": r["rel_type"],
        "child_part_num": r["child_part_num"],
        "parent_part_num": r["parent_part_num"],
    }


def _t_elements(r: dict[str, str]) -> dict[str, Any]:
    return {
        "element_id": r["element_id"],
        "part_num": _nz(r["part_num"]),
        "color_id": _to_int(r["color_id"]),
        "design_id": _nz(r.get("design_id", "")),
    }


def _t_minifigs(r: dict[str, str]) -> dict[str, Any]:
    return {
        "fig_num": r["fig_num"],
        "name": r["name"],
        "num_parts": _to_int(r["num_parts"]),
        "img_url": _nz(r.get("img_url", "")),
    }


def _t_sets(r: dict[str, str]) -> dict[str, Any]:
    return {
        "set_num": r["set_num"],
        "name": r["name"],
        "year": _to_int(r["year"]),
        "theme_id": _to_int(r["theme_id"]),
        "num_parts": _to_int(r["num_parts"]),
        "img_url": _nz(r.get("img_url", "")),
    }


def _t_inventories(r: dict[str, str]) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "version": int(r["version"] or 1),
        "set_num": r["set_num"],
    }


def _t_inventory_parts(r: dict[str, str]) -> dict[str, Any]:
    return {
        "inventory_id": int(r["inventory_id"]),
        "part_num": r["part_num"],
        "color_id": int(r["color_id"]),
        "quantity": int(r["quantity"]),
        "is_spare": _to_bool(r["is_spare"]),
        "img_url": _nz(r.get("img_url", "")),
    }


def _t_inventory_sets(r: dict[str, str]) -> dict[str, Any]:
    return {
        "inventory_id": int(r["inventory_id"]),
        "set_num": r["set_num"],
        "quantity": int(r["quantity"]),
    }


def _t_inventory_minifigs(r: dict[str, str]) -> dict[str, Any]:
    return {
        "inventory_id": int(r["inventory_id"]),
        "fig_num": r["fig_num"],
        "quantity": int(r["quantity"]),
    }


CSV_SPECS: list[CsvSpec] = [
    CsvSpec("themes", Theme, ["id", "name", "parent_id"], _t_themes),
    CsvSpec("colors", Color, ["id", "name", "rgb", "is_trans"], _t_colors),
    CsvSpec("part_categories", PartCategory, ["id", "name"], _t_part_categories),
    CsvSpec(
        "parts",
        Part,
        ["part_num", "name", "part_cat_id", "part_material"],
        _t_parts,
    ),
    CsvSpec(
        "part_relationships",
        PartRelationship,
        ["rel_type", "child_part_num", "parent_part_num"],
        _t_part_relationships,
    ),
    CsvSpec(
        "elements",
        Element,
        ["element_id", "part_num", "color_id", "design_id"],
        _t_elements,
    ),
    CsvSpec(
        "minifigs",
        Minifig,
        ["fig_num", "name", "num_parts", "img_url"],
        _t_minifigs,
    ),
    CsvSpec(
        "sets",
        Set,
        ["set_num", "name", "year", "theme_id", "num_parts", "img_url"],
        _t_sets,
    ),
    CsvSpec("inventories", Inventory, ["id", "version", "set_num"], _t_inventories),
    CsvSpec(
        "inventory_parts",
        InventoryPart,
        ["inventory_id", "part_num", "color_id", "quantity", "is_spare", "img_url"],
        _t_inventory_parts,
    ),
    CsvSpec(
        "inventory_sets",
        InventorySet,
        ["inventory_id", "set_num", "quantity"],
        _t_inventory_sets,
    ),
    CsvSpec(
        "inventory_minifigs",
        InventoryMinifig,
        ["inventory_id", "fig_num", "quantity"],
        _t_inventory_minifigs,
    ),
]


# ---------- Download + import ----------


def _download(name: str, dest: Path, client: httpx.Client) -> Path:
    url = f"{REBRICKABLE_CDN}/{name}.csv.gz"
    tmp = dest.with_suffix(".tmp")
    log.info("Downloading %s", url)
    with client.stream("GET", url) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    tmp.replace(dest)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_rows(path_or_bytes: Path | bytes, columns: list[str]):
    if isinstance(path_or_bytes, Path):
        stream: io.TextIOBase = io.TextIOWrapper(
            gzip.open(path_or_bytes, "rb"), encoding="utf-8", newline=""
        )
    else:
        stream = io.TextIOWrapper(
            gzip.GzipFile(fileobj=io.BytesIO(path_or_bytes), mode="rb"),
            encoding="utf-8",
            newline="",
        )
    with stream:
        reader = csv.DictReader(stream)
        missing = set(columns) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"CSV missing expected columns {missing}; got {reader.fieldnames}"
            )
        yield from reader


def _chunked(iterable, size: int):
    chunk: list[Any] = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _import_csv(
    session: Session,
    spec: CsvSpec,
    source: Path | bytes,
    *,
    chunk_size: int = 5000,
) -> int:
    """Replace-all strategy: delete then bulk insert inside one transaction."""
    table = spec.model.__table__
    session.execute(delete(table))
    count = 0
    for chunk in _chunked(
        (spec.transform(r) for r in _iter_rows(source, spec.columns)), chunk_size
    ):
        session.execute(insert(table), chunk)
        count += len(chunk)
    return count


def _already_imported(session: Session, csv_name: str, sha256: str) -> bool:
    q = (
        select(CatalogImportLog)
        .where(CatalogImportLog.csv_name == csv_name)
        .order_by(CatalogImportLog.imported_at.desc())
        .limit(1)
    )
    row = session.execute(q).scalar_one_or_none()
    return row is not None and row.sha256 == sha256


def run(
    *,
    force: bool = False,
    specs: list[CsvSpec] | None = None,
    http_client: httpx.Client | None = None,
) -> dict[str, int]:
    """Download all CSVs, import new ones, archive raw files. Returns row counts."""
    create_all()
    settings = get_settings()
    download_dir = settings.data_dir / "rebrickable"
    download_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = settings.data_dir / "rebrickable-archive" / datetime.utcnow().strftime(
        "%Y-%m-%d"
    )

    results: dict[str, int] = {}
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        for spec in specs or CSV_SPECS:
            dest = download_dir / f"{spec.name}.csv.gz"
            _download(spec.name, dest, client)
            digest = _sha256(dest)

            with session_scope() as session:
                if not force and _already_imported(session, spec.name, digest):
                    log.info("Skipping %s: unchanged (%s)", spec.name, digest[:12])
                    results[spec.name] = -1
                    continue
                rows = _import_csv(session, spec, dest)
                session.add(
                    CatalogImportLog(
                        csv_name=spec.name, row_count=rows, sha256=digest
                    )
                )
                results[spec.name] = rows
                log.info("Imported %s: %d rows", spec.name, rows)

            archive_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, archive_dir / dest.name)
    finally:
        if owns_client:
            client.close()
    return results
