"""
Unit tests for ``backend.db``.

Each test redirects ``db.DB_PATH`` to an isolated temp file via the ``db_tmp``
fixture below. That fixture also closes any cached connection before and after
the test so state does not leak between tests.

Covered scenarios:
- Schema creation and seed data
- Watchlist add / remove / get / duplicate handling
- Buy: cash debit, weighted-average cost, running position
- Sell: cash credit, quantity decrement, zero-quantity deletion
- Insufficient cash and oversell error reporting (and no-mutation guarantee)
- Portfolio snapshot recording and retrieval ordering
- Chat message add / history retrieval with actions JSON
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend import db


@pytest.fixture
def db_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point ``db.DB_PATH`` at a temp file and initialise a fresh database."""
    db.close_db()
    tmp_db = tmp_path / "finally_test.db"
    monkeypatch.setattr(db, "DB_PATH", tmp_db)
    db.init_db()
    yield tmp_db
    db.close_db()


# -- schema / seed ------------------------------------------------------------

def test_init_db_creates_schema_and_seeds(db_tmp: Path):
    assert db.get_cash_balance() == 10000.0
    tickers = db.get_watchlist_tickers()
    assert set(tickers) == set(db.DEFAULT_WATCHLIST)
    assert len(tickers) == 10


def test_init_db_is_idempotent(db_tmp: Path):
    # Calling init_db() a second time should neither wipe nor duplicate seed data.
    db.init_db()
    assert db.get_cash_balance() == 10000.0
    assert len(db.get_watchlist_tickers()) == 10


def test_init_db_does_not_reseed_after_manual_removal(db_tmp: Path):
    # If the user removes a ticker, a subsequent init_db() should NOT re-seed
    # the full default list.
    assert db.remove_watchlist_ticker("AAPL") is True
    db.init_db()
    assert "AAPL" not in db.get_watchlist_tickers()


# -- watchlist ----------------------------------------------------------------

def test_add_new_watchlist_ticker(db_tmp: Path):
    assert db.add_watchlist_ticker("PYPL") is True
    assert "PYPL" in db.get_watchlist_tickers()


def test_add_duplicate_watchlist_ticker_returns_false(db_tmp: Path):
    db.add_watchlist_ticker("PYPL")
    assert db.add_watchlist_ticker("PYPL") is False
    # Case-insensitive: adding lowercase should also be treated as duplicate.
    assert db.add_watchlist_ticker("pypl") is False
    assert db.get_watchlist_tickers().count("PYPL") == 1


def test_add_watchlist_ticker_normalizes_case(db_tmp: Path):
    db.remove_watchlist_ticker("AAPL")
    assert db.add_watchlist_ticker("aapl") is True
    assert "AAPL" in db.get_watchlist_tickers()


def test_remove_watchlist_ticker(db_tmp: Path):
    assert db.remove_watchlist_ticker("AAPL") is True
    assert "AAPL" not in db.get_watchlist_tickers()


def test_remove_missing_watchlist_ticker_returns_false(db_tmp: Path):
    assert db.remove_watchlist_ticker("DOESNOTEXIST") is False


def test_add_watchlist_ticker_rejects_empty(db_tmp: Path):
    with pytest.raises(ValueError):
        db.add_watchlist_ticker("")


# -- buy trades ---------------------------------------------------------------

def test_buy_creates_position_and_debits_cash(db_tmp: Path):
    result = db.execute_trade("AAPL", "buy", 10, 150.0)
    assert result["success"] is True
    assert result["executed_quantity"] == 10
    assert result["executed_price"] == 150.0
    assert result["error"] is None

    assert db.get_cash_balance() == pytest.approx(10000.0 - 10 * 150.0)

    positions = db.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p["ticker"] == "AAPL"
    assert p["quantity"] == pytest.approx(10)
    assert p["avg_cost"] == pytest.approx(150.0)


def test_buy_weighted_average_cost(db_tmp: Path):
    # 10 @ 100 then 10 @ 200 -> avg 150.
    db.execute_trade("AAPL", "buy", 10, 100.0)
    db.execute_trade("AAPL", "buy", 10, 200.0)

    positions = db.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p["quantity"] == pytest.approx(20)
    assert p["avg_cost"] == pytest.approx(150.0)
    # Cash: 10000 - 1000 - 2000 = 7000.
    assert db.get_cash_balance() == pytest.approx(7000.0)


def test_buy_insufficient_cash_leaves_state_unchanged(db_tmp: Path):
    result = db.execute_trade("AAPL", "buy", 1000, 50.0)  # 50,000 > 10,000
    assert result["success"] is False
    assert "insufficient cash" in result["error"]
    assert result["executed_quantity"] == 0

    assert db.get_cash_balance() == 10000.0
    assert db.get_positions() == []


def test_buy_rejects_nonpositive_quantity(db_tmp: Path):
    result = db.execute_trade("AAPL", "buy", 0, 100.0)
    assert result["success"] is False
    assert "quantity" in result["error"]
    assert db.get_cash_balance() == 10000.0


def test_buy_rejects_nonpositive_price(db_tmp: Path):
    result = db.execute_trade("AAPL", "buy", 1, -5.0)
    assert result["success"] is False
    assert "price" in result["error"]


def test_invalid_side_rejected(db_tmp: Path):
    result = db.execute_trade("AAPL", "hodl", 1, 100.0)
    assert result["success"] is False
    assert "side" in result["error"]


# -- sell trades --------------------------------------------------------------

def test_sell_credits_cash_and_decrements_position(db_tmp: Path):
    db.execute_trade("AAPL", "buy", 10, 150.0)
    result = db.execute_trade("AAPL", "sell", 4, 160.0)
    assert result["success"] is True

    # Cash: 10000 - 1500 + 640 = 9140
    assert db.get_cash_balance() == pytest.approx(10000.0 - 1500.0 + 640.0)

    positions = db.get_positions()
    assert len(positions) == 1
    assert positions[0]["quantity"] == pytest.approx(6)
    # avg_cost is unchanged on sell.
    assert positions[0]["avg_cost"] == pytest.approx(150.0)


def test_sell_to_zero_deletes_position(db_tmp: Path):
    db.execute_trade("AAPL", "buy", 10, 150.0)
    result = db.execute_trade("AAPL", "sell", 10, 160.0)
    assert result["success"] is True

    positions = db.get_positions()
    assert positions == []
    # Cash: 10000 - 1500 + 1600 = 10100
    assert db.get_cash_balance() == pytest.approx(10100.0)


def test_oversell_leaves_state_unchanged(db_tmp: Path):
    db.execute_trade("AAPL", "buy", 5, 100.0)
    cash_before = db.get_cash_balance()
    result = db.execute_trade("AAPL", "sell", 10, 100.0)

    assert result["success"] is False
    assert "oversell" in result["error"]
    assert db.get_cash_balance() == cash_before
    positions = db.get_positions()
    assert len(positions) == 1
    assert positions[0]["quantity"] == pytest.approx(5)


def test_sell_with_no_position_reports_oversell(db_tmp: Path):
    result = db.execute_trade("AAPL", "sell", 1, 100.0)
    assert result["success"] is False
    assert "oversell" in result["error"]


# -- portfolio snapshots ------------------------------------------------------

def test_portfolio_snapshots_record_and_retrieve(db_tmp: Path):
    db.record_portfolio_snapshot(10000.0)
    db.record_portfolio_snapshot(10250.5)
    db.record_portfolio_snapshot(9800.0)

    snaps = db.get_portfolio_snapshots()
    assert len(snaps) == 3
    # Retrieved oldest-first for plotting convenience.
    values = [s["total_value"] for s in snaps]
    assert values == [10000.0, 10250.5, 9800.0]
    for s in snaps:
        assert "recorded_at" in s


def test_portfolio_snapshots_limit(db_tmp: Path):
    for i in range(5):
        db.record_portfolio_snapshot(10000.0 + i)
    snaps = db.get_portfolio_snapshots(limit=3)
    assert len(snaps) == 3


# -- chat messages ------------------------------------------------------------

def test_add_chat_message_returns_id(db_tmp: Path):
    msg_id = db.add_chat_message("user", "hello")
    assert isinstance(msg_id, str)
    assert len(msg_id) > 0


def test_chat_history_roundtrip(db_tmp: Path):
    db.add_chat_message("user", "buy AAPL")
    db.add_chat_message(
        "assistant",
        "Bought 1 AAPL.",
        actions={"trades": [{"ticker": "AAPL", "status": "executed"}]},
    )

    history = db.get_chat_history()
    assert len(history) == 2
    # Oldest-first.
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "buy AAPL"
    assert history[0]["actions"] is None
    assert history[1]["role"] == "assistant"
    assert history[1]["actions"] == {
        "trades": [{"ticker": "AAPL", "status": "executed"}]
    }


def test_chat_history_limit_keeps_most_recent(db_tmp: Path):
    for i in range(25):
        db.add_chat_message("user", f"msg {i}")
    history = db.get_chat_history(limit=20)
    assert len(history) == 20
    # Oldest-first, and we should have dropped the earliest 5.
    contents = [m["content"] for m in history]
    assert contents[0] == "msg 5"
    assert contents[-1] == "msg 24"


def test_add_chat_message_rejects_invalid_role(db_tmp: Path):
    with pytest.raises(ValueError):
        db.add_chat_message("system", "nope")
