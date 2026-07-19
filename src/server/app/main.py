from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from prometheus_client import make_asgi_app
from starlette.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import engine
from app.observability.middleware import ObservabilityMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin) for origin in settings.app_cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ObservabilityMiddleware)
app.include_router(api_router, prefix=settings.app_api_prefix)
app.mount("/metrics", make_asgi_app())


def configure_web_frontend(web_dist_dir: Path | None) -> None:
    if web_dist_dir is None or not (web_dist_dir / "index.html").is_file():

        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, str]:
            return {"name": settings.app_name, "docs": "/docs"}

        return

    web_root = web_dist_dir.resolve()
    assets_dir = web_root / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")

    @app.get("/{requested_path:path}", include_in_schema=False)
    async def serve_web(requested_path: str) -> FileResponse:
        candidate = (web_root / requested_path).resolve()
        if candidate.is_relative_to(web_root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(web_root / "index.html")


configure_web_frontend(settings.web_dist_dir)
