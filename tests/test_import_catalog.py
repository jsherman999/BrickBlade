"""Test CSV import against fixture bytes — no network, no real CSV files."""

from __future__ import annotations

import gzip
import io

import pytest
from sqlalchemy import select

from brickblade.db import models
from brickblade.db.session import create_all, session_scope
from brickblade.jobs.import_catalog import CSV_SPECS, _import_csv


def _gz(text: str) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(text.encode("utf-8"))
    return buf.getvalue()


THEMES_CSV = (
    "id,name,parent_id\n"
    "1,Technic,\n"
    "2,Star Wars,\n"
    "158,Town,1\n"
)

SETS_CSV = (
    "set_num,name,year,theme_id,num_parts,img_url\n"
    "10294-1,Titanic,2021,1,9090,https://example.com/titanic.jpg\n"
    "75192-1,Millennium Falcon,2017,2,7541,https://example.com/falcon.jpg\n"
)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("BRICKBLADE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("BRICKBLADE_DATA_DIR", str(tmp_path))
    # Clear the cached settings + engine so env overrides apply.
    from brickblade import config as cfg
    from brickblade.db import session as db_session

    cfg.get_settings.cache_clear()
    db_session._engine = None
    db_session._SessionLocal = None

    create_all()
    yield


def _spec_for(name: str):
    return next(s for s in CSV_SPECS if s.name == name)


def test_import_themes_and_sets(fresh_db):
    with session_scope() as session:
        n = _import_csv(session, _spec_for("themes"), _gz(THEMES_CSV))
        assert n == 3
    with session_scope() as session:
        n = _import_csv(session, _spec_for("sets"), _gz(SETS_CSV))
        assert n == 2

    with session_scope() as session:
        rows = session.execute(
            select(models.Set).order_by(models.Set.set_num)
        ).scalars().all()
        assert [r.set_num for r in rows] == ["10294-1", "75192-1"]
        assert rows[0].num_parts == 9090
        assert rows[1].theme_id == 2


def test_reimport_replaces_rows(fresh_db):
    updated = (
        "set_num,name,year,theme_id,num_parts,img_url\n"
        "10294-1,Titanic (retired),2021,1,9090,https://example.com/titanic.jpg\n"
    )
    with session_scope() as session:
        _import_csv(session, _spec_for("sets"), _gz(SETS_CSV))
    with session_scope() as session:
        _import_csv(session, _spec_for("sets"), _gz(updated))

    with session_scope() as session:
        rows = session.execute(select(models.Set)).scalars().all()
        assert len(rows) == 1
        assert rows[0].name == "Titanic (retired)"
