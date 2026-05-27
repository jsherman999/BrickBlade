from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from brickblade import __version__
from brickblade.api.deps import AuthRedirect
from brickblade.api.routes import router as api_router
from brickblade.db.session import create_all
from brickblade.web.routes import router as web_router


def create_app() -> FastAPI:
    create_all()
    app = FastAPI(title="BrickBlade", version=__version__)
    app.include_router(api_router)
    app.include_router(web_router)

    @app.exception_handler(AuthRedirect)
    async def _redirect_to_login(_request: Request, _exc: AuthRedirect) -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    return app


app = create_app()
