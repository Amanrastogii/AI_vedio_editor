"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.routes import auth, chat, outputs, processing, projects, timeline, uploads
from backend.api.websocket import router as ws_router
from backend.config import settings
from backend.database.db import engine
from backend.database.models import Base

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _ensure_sqlite_columns(conn) -> None:
    """
    `Base.metadata.create_all` only creates missing TABLES, never adds columns
    to a table that already exists on disk. Since this project has no Alembic
    migrations (schema is create-all-only, by design for local dev), a column
    added to a model after the SQLite file already exists needs a manual
    ADD COLUMN here or every read/write of that column would fail.
    """
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    from sqlalchemy import text
    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(agent_tasks)")).fetchall()}
    if "current_message" not in existing:
        conn.execute(text("ALTER TABLE agent_tasks ADD COLUMN current_message TEXT"))
        logger.info("SQLite migration: added agent_tasks.current_message")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create DB tables on startup (in production use Alembic migrations).
    # Non-fatal: the API still boots if the DB isn't reachable yet, so /health
    # and /docs work during local bring-up before Postgres is online.
    app.state.db_ready = False
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_ensure_sqlite_columns)
        app.state.db_ready = True
        logger.info("Database connected — tables ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Database unavailable at startup (%s). API will serve, but DB-backed "
            "endpoints will fail until Postgres is reachable.", exc,
        )
    logger.info("AI Video Editor API started — v%s", settings.APP_VERSION)
    yield
    await engine.dispose()
    logger.info("API shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-powered video editing platform — 11-agent pipeline",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://your-domain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(projects.router, prefix=API_PREFIX)
app.include_router(uploads.router, prefix=API_PREFIX)
app.include_router(processing.router, prefix=API_PREFIX)
app.include_router(outputs.router, prefix=API_PREFIX)
app.include_router(timeline.router, prefix=API_PREFIX)
app.include_router(chat.router, prefix=API_PREFIX)
app.include_router(ws_router)  # WebSocket (no prefix)

# Serve locally-stored clips/outputs in LOCAL_MODE (StaticFiles supports range
# requests, so the <video> player can stream/seek).
if settings.LOCAL_MODE:
    storage_root = Path(settings.LOCAL_STORAGE_ROOT).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(storage_root)), name="files")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "db_ready": getattr(app.state, "db_ready", False),
    }
