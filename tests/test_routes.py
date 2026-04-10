"""
Unit tests for ``backend.routes`` (portfolio, watchlist, health).

Strategy:
- Build a bare ``FastAPI`` app from the three routers so we can drive each
  route with ``TestClient`` without starting the market data provider lifespan.
- Attach a ``FakeProvider`` to ``app.state.provider`` that returns a controlled
  snapshot dict for ``get_all_prices``.
- Point ``db.DB_PATH`` at an isolated temp file per test so the persistence
  layer gives real, verifiable behaviour without mocking every DB function.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db
from backend.market.types import PriceSnapshot
from backend.routes.health import router as health_router
from backend.routes.portfolio import router as portfolio_router
from backend.routes.watchlist import router as watchlist_router


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class FakeProvider:
    """Minimal stand-in for a MarketDataProvider.

    Tests drive the cache by mutating ``self.prices`` directly; the routes
    only ever call ``get_all_prices``.
    """

    def __init__(self, prices: dict[str, PriceSnapshot] | None = None):
        self.prices: dict[str, PriceSnapshot] = dict(prices or {})

    def get_all_prices(self) -> dict[str, PriceSnapshot]:
        return dict(self.prices)


def _snap(ticker: str, price: float, previous: float | None = None) -> PriceSnapshot:
    prev = previous if previous is not None else price
    direction = "up" if price > prev else "down" if price < prev else "flat"
    return PriceSnapshot(
        ticker=ticker,
        price=price,
        previous_price=prev,
        prev_close=prev,
        timestamp=time.time(),
        direction=direction,
    )


@pytest.fixture
def db_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated temp DB with schema + default seed data."""
    db.close_db()
    tmp_db = tmp_path / "routes_test.db"
    monkeypatch.setattr(db, "DB_PATH", tmp_db)
    db.init_db()
    yield tmp_db
    db.close_db()


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(
        {
            "AAPL": _snap("AAPL", 200.0, previous=198.0),
            "MSFT": _snap("MSFT", 400.0, previous=401.0),
            "TSLA": _snap("TSLA", 250.0),
        }
    )


@pytest.fixture
def client(db_tmp: Path, provider: FakeProvider) -> TestClient:
    """Build a TestClient wrapping a lean app with just the routers under test."""
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(portfolio_router)
    app.include_router(watchlist_router)
    app.state.provider = provider
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------


def test_health_returns_ok_and_provider_name(client: TestClient):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["provider"] == "FakeProvider"


# ---------------------------------------------------------------------------
# /api/portfolio
# ---------------------------------------------------------------------------


def test_portfolio_empty_state(client: TestClient):
    resp = client.get("/api/portfolio")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cash_balance"] == 10000.0
    assert body["positions"] == []
    assert body["total_value"] == 10000.0
    assert body["total_pnl"] == 0.0


def test_portfolio_reflects_positions_and_pnl(client: TestClient):
    # Seed a held position directly via the DB so we can assert P&L math.
    result = db.execute_trade("AAPL", "buy", 10, 150.0)
    assert result["success"], result

    resp = client.get("/api/portfolio")
    assert resp.status_code == 200
    body = resp.json()

    # Cash: 10_000 - 10 * 150 = 8500
    assert body["cash_balance"] == pytest.approx(8500.0)

    assert len(body["positions"]) == 1
    pos = body["positions"][0]
    assert pos["ticker"] == "AAPL"
    assert pos["quantity"] == pytest.approx(10.0)
    assert pos["avg_cost"] == pytest.approx(150.0)
    assert pos["current_price"] == pytest.approx(200.0)
    # Unrealized = (200 - 150) * 10 = 500
    assert pos["unrealized_pnl"] == pytest.approx(500.0)
    # pct = (200 - 150) / 150 * 100 = 33.333...
    assert pos["pnl_pct"] == pytest.approx(33.3333333, rel=1e-4)

    # Total: 8500 cash + 10 * 200 = 10_500
    assert body["total_value"] == pytest.approx(10500.0)
    assert body["total_pnl"] == pytest.approx(500.0)


def test_portfolio_get_records_snapshot(client: TestClient):
    before = len(db.get_portfolio_snapshots())
    client.get("/api/portfolio")
    client.get("/api/portfolio")
    after = len(db.get_portfolio_snapshots())
    assert after == before + 2


def test_portfolio_history_returns_recorded_snapshots(client: TestClient):
    db.record_portfolio_snapshot(10000.0)
    db.record_portfolio_snapshot(10250.0)
    resp = client.get("/api/portfolio/history")
    assert resp.status_code == 200
    body = resp.json()
    assert "snapshots" in body
    values = [s["total_value"] for s in body["snapshots"]]
    assert 10000.0 in values
    assert 10250.0 in values


# ---------------------------------------------------------------------------
# /api/portfolio/trade
# ---------------------------------------------------------------------------


def test_trade_buy_success(client: TestClient):
    resp = client.post(
        "/api/portfolio/trade",
        json={"ticker": "AAPL", "quantity": 5, "side": "buy"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["ticker"] == "AAPL"
    assert body["side"] == "buy"
    assert body["quantity"] == pytest.approx(5.0)
    assert body["price"] == pytest.approx(200.0)

    # Side-effects landed in the DB.
    assert db.get_cash_balance() == pytest.approx(10000.0 - 5 * 200.0)
    positions = db.get_positions()
    assert len(positions) == 1
    assert positions[0]["ticker"] == "AAPL"
    assert positions[0]["quantity"] == pytest.approx(5.0)


def test_trade_sell_success(client: TestClient):
    assert db.execute_trade("AAPL", "buy", 4, 150.0)["success"]

    resp = client.post(
        "/api/portfolio/trade",
        json={"ticker": "AAPL", "quantity": 4, "side": "sell"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["side"] == "sell"

    # Zero-quantity position should be removed entirely.
    assert db.get_positions() == []
    # Cash: 10_000 - 4 * 150 + 4 * 200 = 10_200
    assert db.get_cash_balance() == pytest.approx(10200.0)


def test_trade_lowercases_ticker_and_still_finds_price(client: TestClient):
    resp = client.post(
        "/api/portfolio/trade",
        json={"ticker": "aapl", "quantity": 1, "side": "buy"},
    )
    assert resp.status_code == 200
    assert resp.json()["ticker"] == "AAPL"


def test_trade_insufficient_cash_returns_400(client: TestClient):
    resp = client.post(
        "/api/portfolio/trade",
        json={"ticker": "AAPL", "quantity": 1000, "side": "buy"},
    )
    assert resp.status_code == 400
    assert "insufficient" in resp.json()["detail"].lower()


def test_trade_oversell_returns_400(client: TestClient):
    # No position to sell.
    resp = client.post(
        "/api/portfolio/trade",
        json={"ticker": "AAPL", "quantity": 1, "side": "sell"},
    )
    assert resp.status_code == 400
    assert "oversell" in resp.json()["detail"].lower()


def test_trade_unknown_ticker_returns_400(client: TestClient):
    # ZZZZ is not in the provider cache.
    resp = client.post(
        "/api/portfolio/trade",
        json={"ticker": "ZZZZ", "quantity": 1, "side": "buy"},
    )
    assert resp.status_code == 400
    assert "no live price" in resp.json()["detail"].lower()


def test_trade_rejects_invalid_side(client: TestClient):
    resp = client.post(
        "/api/portfolio/trade",
        json={"ticker": "AAPL", "quantity": 1, "side": "short"},
    )
    assert resp.status_code == 422  # pydantic validation error


def test_trade_rejects_non_positive_quantity(client: TestClient):
    resp = client.post(
        "/api/portfolio/trade",
        json={"ticker": "AAPL", "quantity": 0, "side": "buy"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /api/watchlist
# ---------------------------------------------------------------------------


def test_get_watchlist_enriches_with_prices(client: TestClient):
    resp = client.get("/api/watchlist")
    assert resp.status_code == 200
    body = resp.json()
    assert "tickers" in body

    by_ticker = {t["ticker"]: t for t in body["tickers"]}

    # Seeded default watchlist contains AAPL and MSFT which have prices.
    assert by_ticker["AAPL"]["price"] == pytest.approx(200.0)
    assert by_ticker["AAPL"]["direction"] == "up"
    assert by_ticker["MSFT"]["price"] == pytest.approx(400.0)
    assert by_ticker["MSFT"]["direction"] == "down"

    # A ticker not in the provider cache should come back with null price.
    assert "GOOGL" in by_ticker
    assert by_ticker["GOOGL"]["price"] is None
    assert by_ticker["GOOGL"]["direction"] == "flat"


def test_post_watchlist_add_success(client: TestClient):
    resp = client.post("/api/watchlist", json={"ticker": "pypl"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["ticker"] == "PYPL"
    assert "PYPL" in db.get_watchlist_tickers()


def test_post_watchlist_duplicate_returns_409(client: TestClient):
    # AAPL is in the default seed watchlist.
    resp = client.post("/api/watchlist", json={"ticker": "AAPL"})
    assert resp.status_code == 409
    assert "already" in resp.json()["detail"].lower()


def test_post_watchlist_rejects_empty_ticker(client: TestClient):
    resp = client.post("/api/watchlist", json={"ticker": ""})
    # Pydantic min_length=1 -> 422
    assert resp.status_code == 422


def test_delete_watchlist_success(client: TestClient):
    resp = client.delete("/api/watchlist/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["ticker"] == "AAPL"
    assert "AAPL" not in db.get_watchlist_tickers()


def test_delete_watchlist_unknown_returns_404(client: TestClient):
    resp = client.delete("/api/watchlist/NOSUCH")
    assert resp.status_code == 404
    assert "not on the watchlist" in resp.json()["detail"].lower()
