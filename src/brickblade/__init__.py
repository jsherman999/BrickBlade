"""BrickBlade — LEGO inventory + pricing app."""

__version__ = "0.1.0"


def main() -> None:
    from brickblade.cli import app

    app()
