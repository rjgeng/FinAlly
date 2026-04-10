"""Portfolio REST routes.

Exposes the current portfolio state (positions enriched with live prices and
P&L), a trade endpoint that executes market orders via ``db.execute_trade``,
and a history endpoint that returns recorded portfolio-value snapshots.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db

router = APIRouter()


class TradeRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=16)
    quantity: float = Field(..., gt=0)
    side: Literal["buy", "sell"]


def _build_portfolio_payload(request: Request) -> dict:
    provider = request.app.state.provider
    prices = provider.get_all_prices()

    cash_balance = db.get_cash_balance()
    positions_rows = db.get_positions()

    enriched: list[dict] = []
    positions_value = 0.0
    total_pnl = 0.0
    for row in positions_rows:
        ticker = row["ticker"]
        quantity = float(row["quantity"])
        avg_cost = float(row["avg_cost"])
        snap = prices.get(ticker)
        current_price = float(snap.price) if snap is not None else None

        if current_price is not None:
            market_value = quantity * current_price
            unrealized_pnl = (current_price - avg_cost) * quantity
            pnl_pct = (
                ((current_price - avg_cost) / avg_cost) * 100.0
                if avg_cost > 0
                else 0.0
            )
        else:
            # No price in cache yet — fall back to cost basis so totals still
            # sum sensibly and P&L reads as zero.
            market_value = quantity * avg_cost
            unrealized_pnl = 0.0
            pnl_pct = 0.0

        positions_value += market_value
        total_pnl += unrealized_pnl

        enriched.append(
            {
                "ticker": ticker,
                "quantity": quantity,
                "avg_cost": avg_cost,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "pnl_pct": pnl_pct,
                "updated_at": row["updated_at"],
            }
        )

    total_value = cash_balance + positions_value

    return {
        "cash_balance": cash_balance,
        "positions": enriched,
        "total_value": total_value,
        "total_pnl": total_pnl,
    }


@router.get("/api/portfolio")
async def get_portfolio(request: Request) -> dict:
    """Return the current portfolio and record a fresh snapshot."""
    payload = _build_portfolio_payload(request)
    # Record a snapshot so the history graph picks up every GET call. This is
    # cheap and keeps the snapshot series populated without a separate task.
    db.record_portfolio_snapshot(payload["total_value"])
    return payload


@router.post("/api/portfolio/trade")
async def post_trade(body: TradeRequest, request: Request) -> dict:
    """Execute a market order at the latest cached price."""
    ticker = body.ticker.upper().strip()

    provider = request.app.state.provider
    prices = provider.get_all_prices()
    snap = prices.get(ticker)
    if snap is None:
        raise HTTPException(
            status_code=400,
            detail=f"no live price available for {ticker}",
        )

    price = float(snap.price)
    result = db.execute_trade(
        ticker=ticker,
        side=body.side,
        quantity=body.quantity,
        price=price,
    )

    if not result["success"]:
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or "trade failed",
        )

    return {
        "success": True,
        "ticker": ticker,
        "side": body.side,
        "quantity": float(result["executed_quantity"]),
        "price": float(result["executed_price"]),
        "error": None,
    }


@router.get("/api/portfolio/history")
async def get_history() -> dict:
    """Return recorded portfolio-value snapshots."""
    snapshots = db.get_portfolio_snapshots()
    return {"snapshots": snapshots}
