import typer

app = typer.Typer(help="BrickBlade — LEGO inventory + pricing CLI.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """BrickBlade CLI."""


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


if __name__ == "__main__":
    app()
