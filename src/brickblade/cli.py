import logging
from typing import Annotated

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


# ---------- Config / db ----------


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


@app.command("init-db")
def init_db() -> None:
    """Create tables (idempotent)."""
    from brickblade.db.session import create_all

    create_all()
    typer.echo("tables created")


# ---------- Catalog / prices ----------


@app.command("import-catalog")
def import_catalog(
    force: Annotated[bool, typer.Option(help="Re-import even if unchanged.")] = False,
) -> None:
    """Download Rebrickable CSV dumps and import into the local mirror."""
    from brickblade.jobs.import_catalog import run

    results = run(force=force)
    for name, count in results.items():
        status = "unchanged" if count == -1 else f"{count} rows"
        typer.echo(f"  {name:24s} {status}")


@app.command("refresh-prices")
def refresh_prices(
    stagger: Annotated[float, typer.Option(help="Seconds to sleep between sets.")] = 2.0,
) -> None:
    """Refresh prices for all owned sets."""
    from brickblade.jobs.refresh_prices import run

    result = run(stagger_seconds=stagger)
    typer.echo(
        f"owned={result.owned_sets} "
        f"new_snapshots={result.snapshots_added} "
        f"errors={result.errors}"
    )


# ---------- Inventory ----------


def _build_clients():
    from brickblade.clients.bricklink import BrickLinkClient
    from brickblade.clients.brickset import BricksetClient
    from brickblade.clients.upcitemdb import UpcItemDbClient
    from brickblade.config import get_settings
    from brickblade.core.service import Clients

    s = get_settings()
    return Clients(
        brickset=BricksetClient(s.brickset_key) if s.brickset_key else None,
        bricklink=BrickLinkClient(
            s.bl_consumer_key, s.bl_consumer_secret, s.bl_token, s.bl_token_secret
        )
        if s.bl_consumer_key
        else None,
        upcitemdb=UpcItemDbClient(),
    )


@app.command()
def add(
    set_num: str,
    quantity: Annotated[int, typer.Option("--qty", help="How many you own.")] = 1,
    condition: Annotated[str, typer.Option(help="sealed | used | parted")] = "sealed",
    notes: Annotated[str, typer.Option(help="Freeform note.")] = "",
) -> None:
    """Add a set to your inventory."""
    from brickblade.core.sets import normalize_set_num
    from brickblade.db.models import OwnedSet
    from brickblade.db.session import create_all, session_scope

    create_all()
    canonical = normalize_set_num(set_num)
    with session_scope() as s:
        row = OwnedSet(
            set_num=canonical,
            quantity=quantity,
            condition=condition,
            notes=notes or None,
        )
        s.add(row)
        s.flush()
        typer.echo(f"added id={row.id} {canonical} qty={quantity} ({condition})")


@app.command()
def remove(item_id: int) -> None:
    """Remove a set from your inventory by row id."""
    from sqlalchemy import delete

    from brickblade.db.models import OwnedSet
    from brickblade.db.session import session_scope

    with session_scope() as s:
        result = s.execute(delete(OwnedSet).where(OwnedSet.id == item_id))
        if result.rowcount == 0:
            typer.secho(f"no inventory row with id={item_id}", fg=typer.colors.RED)
            raise typer.Exit(1)
        typer.echo(f"removed id={item_id}")


@app.command("list")
def list_cmd() -> None:
    """Show your inventory."""
    from sqlalchemy import select

    from brickblade.db.models import OwnedSet, Set
    from brickblade.db.session import session_scope

    with session_scope() as s:
        rows = s.execute(
            select(OwnedSet, Set.name)
            .join(Set, Set.set_num == OwnedSet.set_num, isouter=True)
            .order_by(OwnedSet.created_at.desc())
        ).all()
    if not rows:
        typer.echo("(empty)")
        return
    for owned, name in rows:
        typer.echo(
            f"{owned.id:4d}  {owned.set_num:10s}  "
            f"qty={owned.quantity}  {owned.condition:8s}  "
            f"{name or '(not in catalog)'}"
        )


@app.command()
def lookup(
    identifier: str,
    as_barcode: Annotated[bool, typer.Option(help="Treat arg as UPC/EAN.")] = False,
    force: Annotated[bool, typer.Option(help="Force price refresh.")] = False,
) -> None:
    """Lookup a set by number or barcode."""
    from brickblade.core.service import lookup_set
    from brickblade.db.session import session_scope

    clients = _build_clients()
    try:
        with session_scope() as s:
            res = lookup_set(
                s,
                clients=clients,
                barcode=identifier if as_barcode else None,
                set_num=None if as_barcode else identifier,
                force_refresh=force,
            )
    finally:
        if clients.brickset is not None:
            clients.brickset.close()
        if clients.bricklink is not None:
            clients.bricklink.close()
        if clients.upcitemdb is not None:
            clients.upcitemdb.close()

    if res is None:
        typer.secho("not found", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(f"{res.set_num}  {res.metadata.name}  ({res.metadata.theme_name})")
    typer.echo(f"  year={res.metadata.year} parts={res.metadata.num_parts}")
    for p in res.prices:
        price = f"${p.avg_price:.2f}" if p.avg_price is not None else "n/a"
        typer.echo(f"  {p.source:10s} {p.condition:8s} avg={price}")


@app.command()
def value(
    theme: Annotated[str, typer.Option(help="Filter by theme name (substring).")] = "",
) -> None:
    """Sum the latest known price for each owned set (sealed → used fallback)."""
    from sqlalchemy import select

    from brickblade.db.models import OwnedSet, PriceSnapshot, Set, Theme
    from brickblade.db.session import session_scope

    totals: dict[str, float] = {}
    with session_scope() as s:
        q = (
            select(OwnedSet, Set.name, Theme.name)
            .join(Set, Set.set_num == OwnedSet.set_num, isouter=True)
            .join(Theme, Theme.id == Set.theme_id, isouter=True)
        )
        rows = s.execute(q).all()

        for owned, set_name, theme_name in rows:
            if theme and (not theme_name or theme.lower() not in theme_name.lower()):
                continue
            snap = s.execute(
                select(PriceSnapshot)
                .where(PriceSnapshot.set_num == owned.set_num)
                .where(PriceSnapshot.avg_price.isnot(None))
                .order_by(PriceSnapshot.fetched_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            price = float(snap.avg_price) if snap and snap.avg_price else 0.0
            line_total = price * owned.quantity
            totals[owned.set_num] = totals.get(owned.set_num, 0.0) + line_total
            typer.echo(
                f"  {owned.set_num:10s}  qty={owned.quantity}  "
                f"avg=${price:7.2f}  line=${line_total:8.2f}  "
                f"{set_name or ''}"
            )

    grand = sum(totals.values())
    typer.echo(f"\ntotal ({len(totals)} sets): ${grand:,.2f}")


@app.command()
def serve(
    host: str = "0.0.0.0",
    port: int = 8765,
) -> None:
    """Run the FastAPI server (foreground)."""
    import uvicorn

    uvicorn.run("brickblade.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
