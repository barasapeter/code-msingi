from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import Base, engine, run_migrations
from app.routers import api_router
from app.routers.health import router as health_router
from app.web import MEDIA_DIR, router as web_router

BASE_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    """Application factory, useful for tests and alternative configurations."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # For a small starter project this is enough. Use Alembic for production migrations.
        Base.metadata.create_all(bind=engine)
        run_migrations()
        yield

    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    # Covers are public; PDFs are deliberately served only by the download route.
    app.mount("/media/thumbnails", StaticFiles(directory=MEDIA_DIR / "thumbnails"), name="media")
    app.include_router(web_router)
    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
