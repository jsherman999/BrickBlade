from fastapi import FastAPI

from brickblade import __version__
from brickblade.api.routes import router
from brickblade.db.session import create_all


def create_app() -> FastAPI:
    create_all()
    app = FastAPI(title="BrickBlade", version=__version__)
    app.include_router(router)
    return app


app = create_app()
