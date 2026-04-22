"""CLI smoke tests via Typer's CliRunner."""

from __future__ import annotations

import pytest
from sqlalchemy import insert
from typer.testing import CliRunner

from brickblade.cli import app
from brickblade.db import models
from brickblade.db.session import create_all, session_scope


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BRICKBLADE_DB_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setenv("BRICKBLADE_DATA_DIR", str(tmp_path))
    from brickblade import config as cfg
    from brickblade.db import session as db_session

    cfg.get_settings.cache_clear()
    db_session._engine = None
    db_session._SessionLocal = None
    create_all()
    with session_scope() as s:
        s.execute(insert(models.Theme), [{"id": 1, "name": "Creator Expert"}])
        s.execute(
            insert(models.Set),
            [
                {
                    "set_num": "10294-1",
                    "name": "Titanic",
                    "year": 2021,
                    "theme_id": 1,
                    "num_parts": 9090,
                    "img_url": None,
                }
            ],
        )
    yield


def test_cli_health_runs():
    r = CliRunner().invoke(app, ["health"])
    assert r.exit_code == 0
    assert "data_dir" in r.stdout


def test_cli_add_list_remove(fresh_db):
    runner = CliRunner()

    r = runner.invoke(app, ["add", "10294", "--qty", "2"])
    assert r.exit_code == 0, r.stdout
    assert "10294-1" in r.stdout

    r = runner.invoke(app, ["list"])
    assert r.exit_code == 0
    assert "Titanic" in r.stdout
    # First column is the row id.
    row_id = r.stdout.strip().split()[0]

    r = runner.invoke(app, ["remove", row_id])
    assert r.exit_code == 0

    r = runner.invoke(app, ["list"])
    assert "empty" in r.stdout.lower()


def test_cli_remove_missing_errors():
    r = CliRunner().invoke(app, ["remove", "9999"])
    assert r.exit_code == 1


def test_cli_value_empty(fresh_db):
    r = CliRunner().invoke(app, ["value"])
    assert r.exit_code == 0
    assert "$0.00" in r.stdout


def test_cli_value_with_price(fresh_db):
    with session_scope() as s:
        s.add(models.OwnedSet(set_num="10294-1", quantity=2, condition="sealed"))
        s.add(
            models.PriceSnapshot(
                set_num="10294-1",
                source="bricklink",
                condition="used",
                guide_type="sold",
                currency="USD",
                avg_price=500.0,
            )
        )

    r = CliRunner().invoke(app, ["value"])
    assert r.exit_code == 0
    assert "$1,000.00" in r.stdout
