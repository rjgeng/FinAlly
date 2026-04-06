"""
FinAlly backend entry point.

Starts the market data provider on startup, attaches it to app.state so that
route handlers can access it via request.app.state.provider, and tears it down
cleanly on shutdown.

The database module (backend.db) is not yet implemented; the placeholder
`_default_watchlist` function is used until that module is available.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .market.factory import create_provider
from .routes.stream import router as stream_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _default_watchlist() -> list[str]:
    """
    Placeholder watchlist used until the DB module is wired in.
    Replace this with a call to `db.get_watchlist_tickers()` once the
    database layer is implemented.
    """
    return [
        "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA",
        "NVDA", "META", "JPM", "V", "NFLX",
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    logger.info("Starting market data provider...")
    # Replace `_default_watchlist` with `db.get_watchlist_tickers` once the
    # database layer is available.
    provider = create_provider(get_watchlist=_default_watchlist)
    await provider.start()
    app.state.provider = provider
    logger.info("Provider ready: %s", type(provider).__name__)

    yield

    # ---- shutdown ----
    logger.info("Stopping market data provider...")
    await provider.stop()
    logger.info("Provider stopped")


app = FastAPI(title="FinAlly", lifespan=lifespan)

# API routes
app.include_router(stream_router)

# Serve the static Next.js export — must come last so API routes take priority
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
