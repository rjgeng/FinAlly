"""Chat REST route.

Implements ``POST /api/chat`` — the full chat-flow described in
``planning/PLAN.md`` section 9:

1. Load portfolio context (cash, positions with live prices & P&L, watchlist
   with live prices, total value) from the DB + market provider.
2. Load the last 20 chat messages from ``db.get_chat_history()``.
3. Build the messages array for the LLM — a system prompt that establishes the
   FinAlly persona and embeds the portfolio context as JSON, followed by
   history messages and the new user message.
4. Call ``llm.get_llm_response()`` which routes to the real LLM or the mock.
5. Auto-execute any trades and watchlist changes returned by the LLM,
   collecting a deterministic post-execution summary for each.
6. Persist the user message and the assistant message (with the post-execution
   summary serialized into the ``actions`` column).
7. Return ``{message, actions}`` to the frontend.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import db, llm

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Portfolio / context helpers
# ---------------------------------------------------------------------------

def _build_portfolio_context(request: Request) -> dict[str, Any]:
    """Produce the compact portfolio snapshot we embed in the system prompt.

    Mirrors what ``routes/portfolio.py`` computes, but flattens it into the
    shape the LLM system prompt wants. Live prices come from the in-memory
    provider cache; if a ticker has no cached price we fall back to cost basis
    so P&L reads as zero instead of blowing up.
    """
    provider = request.app.state.provider
    prices = provider.get_all_prices()

    cash_balance = db.get_cash_balance()
    position_rows = db.get_positions()

    positions: list[dict[str, Any]] = []
    positions_value = 0.0
    total_pnl = 0.0
    for row in position_rows:
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
            market_value = quantity * avg_cost
            unrealized_pnl = 0.0
            pnl_pct = 0.0

        positions_value += market_value
        total_pnl += unrealized_pnl

        positions.append(
            {
                "ticker": ticker,
                "quantity": quantity,
                "avg_cost": avg_cost,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "pnl_pct": pnl_pct,
            }
        )

    watchlist: list[dict[str, Any]] = []
    for ticker in db.get_watchlist_tickers():
        snap = prices.get(ticker)
        watchlist.append(
            {
                "ticker": ticker,
                "price": float(snap.price) if snap is not None else None,
            }
        )

    total_value = cash_balance + positions_value

    return {
        "cash_balance": cash_balance,
        "positions": positions,
        "watchlist": watchlist,
        "total_value": total_value,
        "total_pnl": total_pnl,
    }


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_HEADER = (
    "You are FinAlly, an AI trading assistant embedded in a simulated trading "
    "workstation. The user has a virtual cash balance and a simulated "
    "portfolio. You can analyze portfolio composition, concentration, and "
    "P&L; suggest trades with concise reasoning; execute trades when the user "
    "asks or agrees; and manage the watchlist proactively. Be concise, "
    "specific, and data-driven. Always return valid structured JSON matching "
    "the response schema — fields: message (str), trades (list of "
    "{ticker, side in [buy, sell], quantity}), watchlist_changes (list of "
    "{ticker, action in [add, remove]})."
)


def _build_messages(
    user_message: str,
    portfolio_context: dict[str, Any],
    history: list[dict[str, Any]],
) -> list[dict[str, str]]:
    system_content = (
        f"{_SYSTEM_PROMPT_HEADER}\n\n"
        f"Current portfolio context (JSON):\n"
        f"{json.dumps(portfolio_context, default=str)}"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    for msg in history:
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


# ---------------------------------------------------------------------------
# Auto-execution helpers
# ---------------------------------------------------------------------------

def _execute_trade(
    instruction: llm.TradeInstruction,
    request: Request,
) -> dict[str, Any]:
    """Execute a single LLM-requested trade and return a summary dict matching
    the schema in PLAN.md section 9.
    """
    ticker = instruction.ticker.upper().strip()
    side = instruction.side.lower().strip()
    requested_quantity = float(instruction.quantity)

    summary: dict[str, Any] = {
        "ticker": ticker,
        "side": side,
        "requested_quantity": requested_quantity,
        "executed_quantity": 0.0,
        "executed_price": None,
        "status": "failed",
        "error": None,
    }

    if side not in ("buy", "sell"):
        summary["error"] = f"invalid side: {side!r}"
        return summary
    if requested_quantity <= 0:
        summary["error"] = "quantity must be positive"
        return summary

    provider = request.app.state.provider
    prices = provider.get_all_prices()
    snap = prices.get(ticker)
    if snap is None:
        summary["error"] = f"no live price available for {ticker}"
        return summary

    price = float(snap.price)
    try:
        result = db.execute_trade(
            ticker=ticker,
            side=side,
            quantity=requested_quantity,
            price=price,
        )
    except Exception as exc:  # noqa: BLE001 — we want the summary to reflect the failure
        logger.exception("execute_trade raised")
        summary["error"] = f"internal error: {exc}"
        return summary

    if result.get("success"):
        summary["executed_quantity"] = float(result.get("executed_quantity") or 0.0)
        summary["executed_price"] = float(result.get("executed_price") or price)
        summary["status"] = "executed"
    else:
        summary["executed_price"] = float(result.get("executed_price") or price)
        summary["error"] = result.get("error") or "trade failed"

    return summary


def _apply_watchlist_change(change: llm.WatchlistChange) -> dict[str, Any]:
    ticker = change.ticker.upper().strip()
    action = change.action.lower().strip()

    summary: dict[str, Any] = {
        "ticker": ticker,
        "action": action,
        "status": "failed",
        "error": None,
    }

    if not ticker:
        summary["error"] = "ticker must be non-empty"
        return summary

    try:
        if action == "add":
            inserted = db.add_watchlist_ticker(ticker)
            if inserted:
                summary["status"] = "applied"
            else:
                # Already present — treat as a no-op success so the assistant
                # doesn't need to apologise for a harmless duplicate.
                summary["status"] = "noop"
        elif action == "remove":
            removed = db.remove_watchlist_ticker(ticker)
            if removed:
                summary["status"] = "applied"
            else:
                summary["status"] = "noop"
        else:
            summary["error"] = f"invalid action: {action!r}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("watchlist change raised")
        summary["error"] = f"internal error: {exc}"

    return summary


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("/api/chat")
async def post_chat(body: ChatRequest, request: Request) -> dict[str, Any]:
    """Handle a single chat turn end-to-end.

    Returns ``{message, actions}`` where ``actions`` is the post-execution
    summary of any trades and watchlist changes the assistant performed.
    """
    user_message = body.message.strip()

    portfolio_context = _build_portfolio_context(request)
    history = db.get_chat_history(limit=20)
    messages = _build_messages(user_message, portfolio_context, history)

    response = llm.get_llm_response(user_message, messages=messages)

    trade_summaries = [_execute_trade(t, request) for t in response.trades]
    watchlist_summaries = [
        _apply_watchlist_change(c) for c in response.watchlist_changes
    ]

    actions_summary: dict[str, Any] = {
        "trades": trade_summaries,
        "watchlist_changes": watchlist_summaries,
    }

    # Persist the turn. User message first so the ordering reads naturally.
    db.add_chat_message("user", user_message)
    db.add_chat_message("assistant", response.message, actions=actions_summary)

    return {"message": response.message, "actions": actions_summary}
