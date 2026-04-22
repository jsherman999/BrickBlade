import logging

import typer

app = typer.Typer(help="BrickBlade — LEGO inventory + pricing CLI.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """BrickBlade CLI."""
    from brickblade.config import get_settings

    logging.basicConfig(
        level=get_settings().brickblade_log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@app.command()
def health() -> None:
    """Print config sanity check."""
    from brickblade.config import get_settings

    s = get_settings()
    typer.echo(f"data_dir:     {s.data_dir}")
    typer.echo(f"db_url:       {s.brickblade_db_url}")
    typer.echo(f"rebrickable:  {'set' if s.rebrickable_key else 'MISSING'}")
    typer.echo(f"brickset:     {'set' if s.brickset_key else 'MISSING'}")
    typer.echo(f"bricklink:    {'set' if s.bl_consumer_key else 'MISSING'}")


@app.command("import-catalog")
def import_catalog(force: bool = typer.Option(False, help="Re-import even if unchanged.")) -> None:
    """Download Rebrickable CSV dumps and import into the local mirror."""
    from brickblade.jobs.import_catalog import run

    results = run(force=force)
    for name, count in results.items():
        status = "unchanged" if count == -1 else f"{count} rows"
        typer.echo(f"  {name:24s} {status}")


@app.command("init-db")
def init_db() -> None:
    """Create tables (idempotent)."""
    from brickblade.db.session import create_all

    create_all()
    typer.echo("tables created")


@app.command("refresh-prices")
def refresh_prices(
    stagger: float = typer.Option(2.0, help="Seconds to sleep between sets."),
) -> None:
    """Refresh prices for all owned sets."""
    from brickblade.jobs.refresh_prices import run

    result = run(stagger_seconds=stagger)
    typer.echo(
        f"owned={result.owned_sets} "
        f"new_snapshots={result.snapshots_added} "
        f"errors={result.errors}"
    )


if __name__ == "__main__":
    app()
