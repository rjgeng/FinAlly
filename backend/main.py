"""
FinAlly backend entry point.

Starts the market data provider on startup, attaches it to app.state so that
route handlers can access it via request.app.state.provider, and tears it down
cleanly on shutdown. The SQLite database is initialised on startup so the
provider's watchlist getter can read live from the ``watchlist`` table.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db
from .market.factory import create_provider
from .routes.health import router as health_router
from .routes.portfolio import router as portfolio_router
from .routes.stream import router as stream_router
from .routes.chat import router as chat_router
from .routes.watchlist import router as watchlist_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    logger.info("Initializing database at %s", db.DB_PATH)
    db.init_db()

    logger.info("Starting market data provider...")
    provider = create_provider(get_watchlist=db.get_watchlist_tickers)
    await provider.start()
    app.state.provider = provider
    logger.info("Provider ready: %s", type(provider).__name__)

    yield

    # ---- shutdown ----
    logger.info("Stopping market data provider...")
    await provider.stop()
    logger.info("Provider stopped")
    db.close_db()


app = FastAPI(title="FinAlly", lifespan=lifespan)

# API routes
app.include_router(health_router)
app.include_router(stream_router)
app.include_router(portfolio_router)
app.include_router(watchlist_router)
app.include_router(chat_router)

# Serve the static Next.js export — must come last so API routes take priority
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
