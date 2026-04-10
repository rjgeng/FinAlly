"""
Unit tests for ``backend.llm`` and ``backend.routes.chat``.

Covers:
- Structured output schema (``LLMResponse`` / ``TradeInstruction`` / ``WatchlistChange``).
- ``mock_response()`` deterministic rules: buy, sell, add, remove, no-action.
- ``get_llm_response()`` routing based on ``LLM_MOCK`` env var.
- ``call_llm()`` with a mocked LiteLLM ``completion`` — both happy and malformed paths.
- ``POST /api/chat`` full chat-flow: portfolio context, auto-execution, DB persistence.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db
from backend.llm import (
    LLMResponse,
    TradeInstruction,
    WatchlistChange,
    call_llm,
    get_llm_response,
    mock_response,
)
from backend.market.types import PriceSnapshot
from backend.routes.chat import router as chat_router


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class FakeProvider:
    """Minimal stand-in for a MarketDataProvider."""

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
    tmp_db = tmp_path / "llm_test.db"
    monkeypatch.setattr(db, "DB_PATH", tmp_db)
    db.init_db()
    yield tmp_db
    db.close_db()


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(
        {
            "AAPL": _snap("AAPL", 200.0, previous=198.0),
            "TSLA": _snap("TSLA", 250.0),
            "NVDA": _snap("NVDA", 900.0, previous=895.0),
        }
    )


@pytest.fixture
def client(db_tmp: Path, provider: FakeProvider) -> TestClient:
    app = FastAPI()
    app.include_router(chat_router)
    app.state.provider = provider
    return TestClient(app)


# ---------------------------------------------------------------------------
# Structured output schema validation
# ---------------------------------------------------------------------------


class TestLLMResponseSchema:
    def test_minimal_message_only(self):
        r = LLMResponse(message="hello")
        assert r.message == "hello"
        assert r.trades == []
        assert r.watchlist_changes == []

    def test_full_response(self):
        r = LLMResponse(
            message="Buying AAPL.",
            trades=[TradeInstruction(ticker="AAPL", side="buy", quantity=10)],
            watchlist_changes=[WatchlistChange(ticker="PYPL", action="add")],
        )
        assert len(r.trades) == 1
        assert r.trades[0].ticker == "AAPL"
        assert r.trades[0].side == "buy"
        assert r.trades[0].quantity == 10
        assert len(r.watchlist_changes) == 1
        assert r.watchlist_changes[0].ticker == "PYPL"
        assert r.watchlist_changes[0].action == "add"

    def test_parse_valid_json_string(self):
        raw = json.dumps(
            {
                "message": "Done.",
                "trades": [{"ticker": "MSFT", "side": "sell", "quantity": 5}],
                "watchlist_changes": [],
            }
        )
        r = LLMResponse.model_validate_json(raw)
        assert r.message == "Done."
        assert r.trades[0].ticker == "MSFT"
        assert r.trades[0].side == "sell"
        assert r.trades[0].quantity == 5.0

    def test_parse_json_missing_optional_fields(self):
        raw = json.dumps({"message": "Hello!"})
        r = LLMResponse.model_validate_json(raw)
        assert r.message == "Hello!"
        assert r.trades == []
        assert r.watchlist_changes == []

    def test_parse_malformed_json_raises(self):
        with pytest.raises(Exception):
            LLMResponse.model_validate_json("not valid json{{{")


# ---------------------------------------------------------------------------
# mock_response deterministic rules
# ---------------------------------------------------------------------------


class TestMockResponse:
    def test_buy_with_explicit_ticker(self):
        r = mock_response("buy TSLA")
        assert len(r.trades) == 1
        assert r.trades[0].ticker == "TSLA"
        assert r.trades[0].side == "buy"
        assert r.trades[0].quantity == 1
        assert not r.watchlist_changes

    def test_buy_defaults_to_aapl(self):
        r = mock_response("buy some shares")
        assert r.trades[0].ticker == "AAPL"
        assert r.trades[0].side == "buy"

    def test_sell_with_explicit_ticker(self):
        r = mock_response("sell NVDA")
        assert len(r.trades) == 1
        assert r.trades[0].ticker == "NVDA"
        assert r.trades[0].side == "sell"
        assert r.trades[0].quantity == 1
        assert not r.watchlist_changes

    def test_sell_defaults_to_aapl(self):
        r = mock_response("sell")
        assert r.trades[0].ticker == "AAPL"
        assert r.trades[0].side == "sell"

    def test_add_ticker_to_watchlist(self):
        r = mock_response("add PYPL")
        assert not r.trades
        assert len(r.watchlist_changes) == 1
        assert r.watchlist_changes[0].ticker == "PYPL"
        assert r.watchlist_changes[0].action == "add"

    def test_remove_ticker_from_watchlist(self):
        r = mock_response("remove NFLX")
        assert not r.trades
        assert len(r.watchlist_changes) == 1
        assert r.watchlist_changes[0].ticker == "NFLX"
        assert r.watchlist_changes[0].action == "remove"

    def test_no_action_message(self):
        r = mock_response("hello")
        assert not r.trades
        assert not r.watchlist_changes
        assert "no action" in r.message.lower()

    def test_buy_takes_priority_over_add(self):
        # "buy" is checked before "add"
        r = mock_response("buy and add TSLA")
        assert r.trades[0].side == "buy"
        assert not r.watchlist_changes

    def test_case_insensitive(self):
        r = mock_response("BUY MSFT")
        assert r.trades[0].ticker == "MSFT"
        assert r.trades[0].side == "buy"


# ---------------------------------------------------------------------------
# get_llm_response routing
# ---------------------------------------------------------------------------


class TestGetLlmResponse:
    def test_mock_mode_routes_to_mock(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        r = get_llm_response("buy TSLA")
        assert r.trades[0].ticker == "TSLA"
        assert r.trades[0].side == "buy"

    def test_mock_mode_case_insensitive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_MOCK", "True")
        r = get_llm_response("hello")
        assert "mock" in r.message.lower()

    def test_non_mock_mode_calls_litellm(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_MOCK", "false")
        valid_json = json.dumps({"message": "From the real LLM"})
        mock_choice = MagicMock()
        mock_choice.message.content = valid_json
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        with patch("litellm.completion", return_value=mock_resp) as patched:
            r = get_llm_response("hello", messages=[{"role": "user", "content": "hello"}])
            patched.assert_called_once()
            assert r.message == "From the real LLM"


# ---------------------------------------------------------------------------
# call_llm with mocked litellm.completion
# ---------------------------------------------------------------------------


class TestCallLlm:
    def test_successful_structured_response(self):
        valid_json = json.dumps(
            {
                "message": "Bought 5 shares of AAPL.",
                "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 5}],
                "watchlist_changes": [],
            }
        )
        mock_choice = MagicMock()
        mock_choice.message.content = valid_json
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        with patch("litellm.completion", return_value=mock_resp):
            r = call_llm([{"role": "user", "content": "buy 5 AAPL"}])
            assert r.message == "Bought 5 shares of AAPL."
            assert r.trades[0].ticker == "AAPL"
            assert r.trades[0].quantity == 5.0

    def test_malformed_json_returns_safe_fallback(self):
        mock_choice = MagicMock()
        mock_choice.message.content = "not json at all {{{broken"
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        with patch("litellm.completion", return_value=mock_resp):
            r = call_llm([{"role": "user", "content": "test"}])
            assert "sorry" in r.message.lower() or "trouble" in r.message.lower()
            assert r.trades == []
            assert r.watchlist_changes == []

    def test_empty_content_returns_safe_fallback(self):
        mock_choice = MagicMock()
        mock_choice.message.content = ""
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        with patch("litellm.completion", return_value=mock_resp):
            r = call_llm([{"role": "user", "content": "test"}])
            assert "sorry" in r.message.lower() or "unavailable" in r.message.lower()
            assert r.trades == []

    def test_network_error_returns_safe_fallback(self):
        with patch("litellm.completion", side_effect=ConnectionError("timeout")):
            r = call_llm([{"role": "user", "content": "test"}])
            assert "sorry" in r.message.lower() or "unavailable" in r.message.lower()
            assert r.trades == []

    def test_none_content_returns_safe_fallback(self):
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        with patch("litellm.completion", return_value=mock_resp):
            r = call_llm([{"role": "user", "content": "test"}])
            assert "sorry" in r.message.lower() or "unavailable" in r.message.lower()


# ---------------------------------------------------------------------------
# POST /api/chat — full chat flow (mock LLM, real DB)
# ---------------------------------------------------------------------------


class TestChatRoute:
    def test_no_action_message(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        resp = client.post("/api/chat", json={"message": "hello"})
        assert resp.status_code == 200
        body = resp.json()
        assert "mock" in body["message"].lower()
        assert body["actions"]["trades"] == []
        assert body["actions"]["watchlist_changes"] == []

    def test_buy_trade_executes_and_updates_portfolio(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LLM_MOCK", "true")
        resp = client.post("/api/chat", json={"message": "buy TSLA"})
        assert resp.status_code == 200
        body = resp.json()

        trades = body["actions"]["trades"]
        assert len(trades) == 1
        assert trades[0]["ticker"] == "TSLA"
        assert trades[0]["side"] == "buy"
        assert trades[0]["status"] == "executed"
        assert trades[0]["executed_quantity"] == 1.0
        assert trades[0]["executed_price"] == 250.0

        # Verify DB side-effects
        assert db.get_cash_balance() == 10000.0 - 250.0
        positions = db.get_positions()
        tsla_pos = [p for p in positions if p["ticker"] == "TSLA"]
        assert len(tsla_pos) == 1
        assert tsla_pos[0]["quantity"] == 1.0

    def test_sell_without_position_fails_gracefully(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LLM_MOCK", "true")
        resp = client.post("/api/chat", json={"message": "sell TSLA"})
        assert resp.status_code == 200
        body = resp.json()
        trades = body["actions"]["trades"]
        assert len(trades) == 1
        assert trades[0]["status"] == "failed"
        assert trades[0]["error"] is not None
        assert "oversell" in trades[0]["error"].lower()

    def test_add_to_watchlist(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LLM_MOCK", "true")
        resp = client.post("/api/chat", json={"message": "add PYPL"})
        assert resp.status_code == 200
        body = resp.json()
        wl = body["actions"]["watchlist_changes"]
        assert len(wl) == 1
        assert wl[0]["ticker"] == "PYPL"
        assert wl[0]["action"] == "add"
        assert wl[0]["status"] == "applied"

        assert "PYPL" in db.get_watchlist_tickers()

    def test_remove_from_watchlist(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LLM_MOCK", "true")
        # AAPL is in the default watchlist seed.
        resp = client.post("/api/chat", json={"message": "remove AAPL"})
        assert resp.status_code == 200
        body = resp.json()
        wl = body["actions"]["watchlist_changes"]
        assert len(wl) == 1
        assert wl[0]["ticker"] == "AAPL"
        assert wl[0]["action"] == "remove"
        assert wl[0]["status"] == "applied"

        assert "AAPL" not in db.get_watchlist_tickers()

    def test_chat_history_persisted(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LLM_MOCK", "true")
        client.post("/api/chat", json={"message": "hello"})
        client.post("/api/chat", json={"message": "how are you?"})

        history = db.get_chat_history(limit=20)
        # 2 turns = 4 messages (user + assistant each)
        assert len(history) == 4
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "hello"
        assert history[1]["role"] == "assistant"
        assert history[2]["role"] == "user"
        assert history[2]["content"] == "how are you?"
        assert history[3]["role"] == "assistant"

    def test_assistant_message_has_actions_stored(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LLM_MOCK", "true")
        client.post("/api/chat", json={"message": "buy AAPL"})

        history = db.get_chat_history(limit=20)
        assistant_msg = [m for m in history if m["role"] == "assistant"][0]
        assert assistant_msg["actions"] is not None
        assert "trades" in assistant_msg["actions"]
        assert assistant_msg["actions"]["trades"][0]["ticker"] == "AAPL"

    def test_buy_ticker_without_price_fails_gracefully(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """If the LLM suggests buying a ticker not in the provider cache,
        the trade should fail but the route should still return 200."""
        monkeypatch.setenv("LLM_MOCK", "true")
        # "buy GOOGL" — GOOGL is not in our FakeProvider
        resp = client.post("/api/chat", json={"message": "buy GOOGL"})
        assert resp.status_code == 200
        body = resp.json()
        trades = body["actions"]["trades"]
        assert len(trades) == 1
        assert trades[0]["status"] == "failed"
        assert "no live price" in trades[0]["error"].lower()

    def test_empty_message_rejected(self, client: TestClient):
        resp = client.post("/api/chat", json={"message": ""})
        assert resp.status_code == 422

    def test_missing_message_rejected(self, client: TestClient):
        resp = client.post("/api/chat", json={})
        assert resp.status_code == 422
