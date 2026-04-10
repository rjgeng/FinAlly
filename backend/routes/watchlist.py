"""Watchlist REST routes.

Reads the tickers from the database and enriches each entry with the latest
cached price from the market data provider.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db

router = APIRouter()


class WatchlistAddRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=16)


@router.get("/api/watchlist")
async def get_watchlist(request: Request) -> dict:
    """Return all watched tickers with their latest cached prices.

    Tickers not yet present in the provider cache return ``price: null``.
    """
    provider = request.app.state.provider
    prices = provider.get_all_prices()

    tickers = db.get_watchlist_tickers()
    items: list[dict] = []
    for ticker in tickers:
        snap = prices.get(ticker)
        if snap is None:
            items.append(
                {
                    "ticker": ticker,
                    "price": None,
                    "previous_price": None,
                    "direction": "flat",
                    "timestamp": None,
                }
            )
        else:
            items.append(
                {
                    "ticker": ticker,
                    "price": snap.price,
                    "previous_price": snap.previous_price,
                    "direction": snap.direction,
                    "timestamp": snap.timestamp,
                }
            )
    return {"tickers": items}


@router.post("/api/watchlist")
async def add_watchlist(body: WatchlistAddRequest) -> dict:
    """Add a ticker to the watchlist (uppercased)."""
    ticker = body.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker must not be empty")

    inserted = db.add_watchlist_ticker(ticker)
    if not inserted:
        raise HTTPException(
            status_code=409,
            detail=f"{ticker} is already on the watchlist",
        )
    return {"success": True, "ticker": ticker, "message": f"added {ticker}"}


@router.delete("/api/watchlist/{ticker}")
async def remove_watchlist(ticker: str) -> dict:
    """Remove a ticker from the watchlist."""
    ticker = ticker.upper().strip()
    removed = db.remove_watchlist_ticker(ticker)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"{ticker} is not on the watchlist",
        )
    return {"success": True, "ticker": ticker}
