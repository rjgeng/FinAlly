"""
Unit tests for MassiveProvider.

These tests cover:
- _extract_price logic (prefers lastTrade.p, falls back to day.c, returns None)
- _process_response: skips unchanged prices, updates on price change, direction
- set_watchlist: evicts removed tickers, updates internal list
- _fetch_and_update: awaits the httpx client (regression), handles HTTP errors
- Async start/stop lifecycle
- Interface conformance
"""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.market.massive_provider import MassiveProvider
from backend.market.types import PriceSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_provider(tickers: list[str] | None = None) -> MassiveProvider:
    watchlist = tickers if tickers is not None else ["AAPL"]
    return MassiveProvider(api_key="test-key", get_watchlist=lambda: watchlist)


def _snap(ticker: str, price: float, prev: float = 0.0, close: float = 0.0) -> PriceSnapshot:
    return PriceSnapshot(
        ticker=ticker,
        price=price,
        previous_price=prev,
        prev_close=close,
        timestamp=time.time(),
        direction="flat",
    )


def make_raw_result(
    ticker: str,
    last_trade_price: float | None = None,
    day_close: float | None = None,
    prev_close: float = 0.0,
) -> dict:
    result: dict = {"ticker": ticker, "prevDay": {"c": prev_close}}
    if last_trade_price is not None:
        result["lastTrade"] = {"p": last_trade_price}
    if day_close is not None:
        result["day"] = {"c": day_close}
    return result


# ---------------------------------------------------------------------------
# _extract_price
# ---------------------------------------------------------------------------

def test_extract_price_prefers_last_trade():
    raw = {"lastTrade": {"p": 192.5}, "day": {"c": 190.0}}
    assert MassiveProvider._extract_price(raw) == 192.5


def test_extract_price_falls_back_to_day_close():
    raw = {"lastTrade": {}, "day": {"c": 190.0}}
    assert MassiveProvider._extract_price(raw) == 190.0


def test_extract_price_returns_none_when_both_missing():
    raw = {"lastTrade": {}, "day": {}}
    assert MassiveProvider._extract_price(raw) is None


def test_extract_price_returns_none_when_fields_absent():
    assert MassiveProvider._extract_price({}) is None


def test_extract_price_handles_none_sub_dicts():
    raw = {"lastTrade": None, "day": None}
    assert MassiveProvider._extract_price(raw) is None


def test_extract_price_handles_non_numeric_value():
    raw = {"lastTrade": {"p": "not_a_number"}, "day": {"c": "also_bad"}}
    assert MassiveProvider._extract_price(raw) is None


def test_extract_price_converts_string_numeric():
    raw = {"lastTrade": {"p": "195.60"}}
    assert MassiveProvider._extract_price(raw) == 195.60


# ---------------------------------------------------------------------------
# _process_response
# ---------------------------------------------------------------------------

def test_process_response_populates_cache_for_new_ticker():
    provider = make_provider(["AAPL"])
    provider._process_response({
        "tickers": [make_raw_result("AAPL", last_trade_price=185.0, prev_close=184.0)]
    })
    snap = provider.get_price("AAPL")
    assert snap is not None
    assert snap.price == 185.0


def test_process_response_skips_unchanged_price():
    provider = make_provider(["AAPL"])
    existing = _snap("AAPL", 185.0)
    provider._cache["AAPL"] = existing

    provider._process_response({
        "tickers": [make_raw_result("AAPL", last_trade_price=185.0, prev_close=183.0)]
    })

    # Cache object must be the same — no update happened
    assert provider._cache["AAPL"] is existing


def test_process_response_updates_on_price_change():
    provider = make_provider(["AAPL"])
    provider._cache["AAPL"] = _snap("AAPL", 185.0, prev=184.0, close=183.0)

    provider._process_response({
        "tickers": [make_raw_result("AAPL", last_trade_price=186.0, prev_close=183.0)]
    })

    snap = provider.get_price("AAPL")
    assert snap.price == 186.0
    assert snap.previous_price == 185.0
    assert snap.prev_close == 183.0


def test_process_response_direction_up():
    provider = make_provider(["AAPL"])
    provider._cache["AAPL"] = _snap("AAPL", 185.0)
    provider._process_response({
        "tickers": [make_raw_result("AAPL", last_trade_price=186.0)]
    })
    assert provider.get_price("AAPL").direction == "up"


def test_process_response_direction_down():
    provider = make_provider(["AAPL"])
    provider._cache["AAPL"] = _snap("AAPL", 185.0)
    provider._process_response({
        "tickers": [make_raw_result("AAPL", last_trade_price=184.0)]
    })
    assert provider.get_price("AAPL").direction == "down"


def test_process_response_skips_result_without_ticker():
    provider = make_provider([])
    # Should not raise
    provider._process_response({"tickers": [{"lastTrade": {"p": 100.0}}]})
    assert provider.get_all_prices() == {}


def test_process_response_skips_result_with_no_price():
    provider = make_provider(["AAPL"])
    # No lastTrade or day.c
    provider._process_response({"tickers": [{"ticker": "AAPL"}]})
    assert provider.get_price("AAPL") is None


def test_process_response_accepts_results_key():
    """Some Massive API endpoints use 'results' not 'tickers'."""
    provider = make_provider(["AAPL"])
    provider._process_response({
        "results": [make_raw_result("AAPL", last_trade_price=190.0)]
    })
    assert provider.get_price("AAPL").price == 190.0


def test_process_response_first_snapshot_uses_new_price_as_previous():
    """When there is no existing cache entry, previous_price == new_price."""
    provider = make_provider(["AAPL"])
    provider._process_response({
        "tickers": [make_raw_result("AAPL", last_trade_price=195.0)]
    })
    snap = provider.get_price("AAPL")
    assert snap.previous_price == 195.0


# ---------------------------------------------------------------------------
# set_watchlist
# ---------------------------------------------------------------------------

def test_set_watchlist_evicts_removed_tickers():
    provider = make_provider(["AAPL", "TSLA"])
    provider._cache["TSLA"] = _snap("TSLA", 175.0)
    provider._watchlist = ["AAPL", "TSLA"]

    provider.set_watchlist(["AAPL"])

    assert "TSLA" not in provider._cache


def test_set_watchlist_updates_internal_list():
    provider = make_provider(["AAPL"])
    provider._watchlist = ["AAPL"]
    provider.set_watchlist(["AAPL", "MSFT"])
    assert "MSFT" in provider._watchlist


def test_set_watchlist_does_not_evict_retained_tickers():
    provider = make_provider(["AAPL", "MSFT"])
    provider._cache["AAPL"] = _snap("AAPL", 185.0)
    provider._watchlist = ["AAPL", "MSFT"]

    provider.set_watchlist(["AAPL"])   # remove MSFT, keep AAPL

    assert "AAPL" in provider._cache


def test_set_watchlist_empty_evicts_all():
    provider = make_provider(["AAPL", "TSLA"])
    provider._cache["AAPL"] = _snap("AAPL", 185.0)
    provider._cache["TSLA"] = _snap("TSLA", 175.0)
    provider._watchlist = ["AAPL", "TSLA"]

    provider.set_watchlist([])

    assert provider._cache == {}


# ---------------------------------------------------------------------------
# _fetch_and_update (httpx mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_and_update_awaits_client_get():
    """_fetch_and_update must await the httpx async client (regression test)."""
    provider = make_provider(["AAPL"])

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "tickers": [make_raw_result("AAPL", last_trade_price=190.0, prev_close=188.0)]
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    provider._client = mock_client

    await provider._fetch_and_update(["AAPL"])

    mock_client.get.assert_awaited_once()
    assert provider.get_price("AAPL").price == 190.0


@pytest.mark.asyncio
async def test_fetch_and_update_logs_http_error(caplog):
    """HTTP errors should be logged without raising."""
    import httpx
    import logging

    provider = make_provider(["AAPL"])

    mock_response = MagicMock()
    mock_response.status_code = 429
    http_error = httpx.HTTPStatusError(
        "Too Many Requests", request=MagicMock(), response=mock_response
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=http_error)
    provider._client = mock_client

    with caplog.at_level(logging.ERROR, logger="backend.market.massive_provider"):
        await provider._fetch_and_update(["AAPL"])

    assert any("HTTP error" in r.message or "429" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_and_update_logs_generic_error(caplog):
    """Network errors should be logged without raising."""
    import logging

    provider = make_provider(["AAPL"])

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=ConnectionError("timeout"))
    provider._client = mock_client

    with caplog.at_level(logging.ERROR, logger="backend.market.massive_provider"):
        await provider._fetch_and_update(["AAPL"])

    assert any("error" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_and_update_raises_if_not_started():
    """Calling _fetch_and_update without start() should raise AssertionError."""
    provider = make_provider(["AAPL"])
    # _client is None — provider not started
    with pytest.raises(AssertionError):
        await provider._fetch_and_update(["AAPL"])


# ---------------------------------------------------------------------------
# get_all_prices returns a copy
# ---------------------------------------------------------------------------

def test_get_all_prices_returns_copy():
    provider = make_provider(["AAPL"])
    provider._cache["AAPL"] = _snap("AAPL", 185.0)
    copy = provider.get_all_prices()
    # Mutate the copy — original cache must be unaffected
    copy["AAPL"] = _snap("AAPL", 999.0)
    assert provider._cache["AAPL"].price == 185.0


# ---------------------------------------------------------------------------
# Async lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_stop_no_task_leak():
    """Provider should start and stop cleanly."""
    # Use a fake watchlist so no real HTTP calls happen
    provider = MassiveProvider(api_key="dummy", get_watchlist=lambda: [])
    await provider.start()
    await provider.stop()
    assert provider._task is None
    assert provider._client is None


@pytest.mark.asyncio
async def test_stop_before_start_is_safe():
    """Calling stop() before start() should not raise."""
    provider = make_provider([])
    await provider.stop()  # should be a no-op


# ---------------------------------------------------------------------------
# Interface conformance
# ---------------------------------------------------------------------------

def test_massive_provider_is_market_data_provider():
    from backend.market.base import MarketDataProvider
    provider = make_provider()
    assert isinstance(provider, MarketDataProvider)
