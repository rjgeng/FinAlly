"""
Unit tests for SimulatorProvider.

These tests cover:
- Seed prices and cache population
- GBM tick mechanics (price positivity, direction field, previous_price)
- Watchlist add/remove
- get_all_prices returns a copy
- Unknown ticker handling
- Fallback seed price for unknown tickers
- Async start/stop lifecycle
- Prices stay positive over many ticks
- Correlated moves (high-beta tickers co-move positively)
"""
import asyncio
import math
import random

import pytest

from backend.market.simulator import (
    SimulatorProvider,
    SEED_PRICES,
    DEFAULT_SEED_PRICE,
    PRICE_FLOOR,
)
from backend.market.types import PriceSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_provider(tickers: list[str] | None = None) -> SimulatorProvider:
    watchlist = tickers if tickers is not None else ["AAPL", "MSFT"]
    return SimulatorProvider(get_watchlist=lambda: watchlist)


def _started(tickers: list[str]) -> SimulatorProvider:
    """Create a provider with its cache initialised (no background task)."""
    sim = make_provider(tickers)
    sim._init_tickers(tickers)
    return sim


# ---------------------------------------------------------------------------
# Cache / seed-price tests
# ---------------------------------------------------------------------------

def test_seed_prices_populated_aapl():
    sim = _started(["AAPL"])
    snap = sim.get_price("AAPL")
    assert snap is not None
    assert snap.price == SEED_PRICES["AAPL"]


def test_seed_prices_populated_multiple():
    tickers = ["AAPL", "TSLA", "NVDA"]
    sim = _started(tickers)
    for t in tickers:
        snap = sim.get_price(t)
        assert snap is not None
        assert snap.price == SEED_PRICES[t]


def test_fallback_seed_price_for_unknown_ticker():
    sim = _started(["FAKE"])
    snap = sim.get_price("FAKE")
    assert snap is not None
    assert snap.price == DEFAULT_SEED_PRICE


def test_initial_direction_is_flat():
    sim = _started(["AAPL"])
    snap = sim.get_price("AAPL")
    assert snap.direction == "flat"


def test_initial_previous_price_equals_price():
    sim = _started(["AAPL"])
    snap = sim.get_price("AAPL")
    assert snap.previous_price == snap.price


def test_initial_prev_close_equals_seed():
    sim = _started(["MSFT"])
    snap = sim.get_price("MSFT")
    assert snap.prev_close == SEED_PRICES["MSFT"]


# ---------------------------------------------------------------------------
# Tick mechanics
# ---------------------------------------------------------------------------

def test_tick_updates_cache():
    sim = _started(["AAPL"])
    sim._tick()
    snap = sim.get_price("AAPL")
    assert snap is not None


def test_tick_previous_price_reflects_pre_tick_price():
    sim = _started(["MSFT"])
    before_price = sim.get_price("MSFT").price
    sim._tick()
    snap = sim.get_price("MSFT")
    assert snap.previous_price == before_price


def test_tick_direction_valid_values():
    sim = _started(["GOOGL"])
    sim._tick()
    snap = sim.get_price("GOOGL")
    assert snap.direction in ("up", "down", "flat")


def test_tick_direction_up_when_price_rises():
    """Force a price increase and verify direction == 'up'."""
    sim = _started(["AAPL"])
    original_price = sim._states["AAPL"].current_price

    # Manually set price higher and update cache via _tick after forcing state
    sim._states["AAPL"].current_price = original_price
    # Monkey-patch random.gauss to always return large positive for one tick
    original_gauss = random.gauss
    random.gauss = lambda mu, sigma: 10.0  # large positive shock -> price up
    sim._tick()
    random.gauss = original_gauss

    snap = sim.get_price("AAPL")
    assert snap.direction == "up"
    assert snap.price > snap.previous_price


def test_tick_price_floor_applied():
    """Even with extreme negative shocks, price must not fall below PRICE_FLOOR."""
    sim = _started(["TSLA"])
    # Force extremely negative noise
    original_gauss = random.gauss
    random.gauss = lambda mu, sigma: -1000.0
    for _ in range(100):
        sim._tick()
    random.gauss = original_gauss
    snap = sim.get_price("TSLA")
    assert snap.price >= PRICE_FLOOR


def test_prices_stay_positive_over_many_ticks():
    """Prices must stay positive after 10,000 ticks (stress test)."""
    sim = _started(["TSLA"])  # high-vol ticker
    for _ in range(10_000):
        sim._tick()
    snap = sim.get_price("TSLA")
    assert snap.price > 0.0


# ---------------------------------------------------------------------------
# Watchlist management
# ---------------------------------------------------------------------------

def test_set_watchlist_adds_new_ticker():
    sim = _started(["AAPL"])
    assert sim.get_price("NFLX") is None
    sim.set_watchlist(["AAPL", "NFLX"])
    assert sim.get_price("NFLX") is not None


def test_set_watchlist_new_ticker_has_seed_price():
    sim = _started(["AAPL"])
    sim.set_watchlist(["AAPL", "JPM"])
    snap = sim.get_price("JPM")
    assert snap is not None
    assert snap.price == SEED_PRICES["JPM"]


def test_set_watchlist_evicts_removed_ticker():
    sim = _started(["AAPL", "MSFT"])
    sim.set_watchlist(["AAPL"])
    assert sim.get_price("MSFT") is None
    assert sim.get_price("AAPL") is not None


def test_set_watchlist_empty_clears_all():
    sim = _started(["AAPL", "TSLA"])
    sim.set_watchlist([])
    assert sim.get_price("AAPL") is None
    assert sim.get_price("TSLA") is None
    assert sim.get_all_prices() == {}


def test_set_watchlist_idempotent():
    """Calling set_watchlist with the same list should not alter prices."""
    sim = _started(["AAPL"])
    price_before = sim.get_price("AAPL").price
    sim.set_watchlist(["AAPL"])
    price_after = sim.get_price("AAPL").price
    # AAPL was already tracked — price should be unchanged (not re-seeded)
    assert price_before == price_after


# ---------------------------------------------------------------------------
# get_all_prices returns a copy
# ---------------------------------------------------------------------------

def test_get_all_prices_returns_copy():
    sim = _started(["AAPL"])
    snapshot = sim.get_all_prices()
    old_price = snapshot["AAPL"].price
    sim._tick()
    # The snapshot captured before the tick must not be mutated
    assert snapshot["AAPL"].price == old_price


def test_get_all_prices_contains_all_tracked():
    tickers = ["AAPL", "MSFT", "TSLA"]
    sim = _started(tickers)
    prices = sim.get_all_prices()
    assert set(prices.keys()) == set(tickers)


# ---------------------------------------------------------------------------
# Unknown ticker
# ---------------------------------------------------------------------------

def test_unknown_ticker_returns_none():
    sim = make_provider([])
    assert sim.get_price("UNKNOWN") is None


# ---------------------------------------------------------------------------
# Correlation test
# ---------------------------------------------------------------------------

def test_high_beta_tickers_positively_correlated():
    """
    Two high-beta tickers (AAPL beta=0.65, MSFT beta=0.65) should have
    meaningfully positive log-return correlation over 1,000 ticks.
    """
    tickers = ["AAPL", "MSFT"]
    sim = _started(tickers)

    log_returns_aapl = []
    log_returns_msft = []

    for _ in range(1_000):
        prev_a = sim._states["AAPL"].current_price
        prev_m = sim._states["MSFT"].current_price
        sim._tick()
        log_returns_aapl.append(math.log(sim._states["AAPL"].current_price / prev_a))
        log_returns_msft.append(math.log(sim._states["MSFT"].current_price / prev_m))

    n = len(log_returns_aapl)
    mean_a = sum(log_returns_aapl) / n
    mean_m = sum(log_returns_msft) / n
    cov = sum(
        (a - mean_a) * (m - mean_m)
        for a, m in zip(log_returns_aapl, log_returns_msft)
    ) / n
    std_a = math.sqrt(sum((a - mean_a) ** 2 for a in log_returns_aapl) / n)
    std_m = math.sqrt(sum((m - mean_m) ** 2 for m in log_returns_msft) / n)
    corr = cov / (std_a * std_m) if std_a * std_m > 0 else 0.0

    assert corr > 0.2, f"Expected positive correlation > 0.2, got {corr:.3f}"


# ---------------------------------------------------------------------------
# GBM mean log-return convergence
# ---------------------------------------------------------------------------

def test_mean_log_return_convergence():
    """
    Over many ticks the mean log-return per tick should converge to
    (drift - 0.5 * sigma²) * dt within a reasonable tolerance.
    """
    sim = _started(["AAPL"])
    state = sim._states["AAPL"]

    n = 50_000
    log_returns = []
    for _ in range(n):
        prev = state.current_price
        sim._tick()
        log_returns.append(math.log(state.current_price / prev))

    from backend.market.simulator import TICK_INTERVAL
    expected = (state.drift - 0.5 * state.volatility ** 2) * TICK_INTERVAL
    observed = sum(log_returns) / n
    # Tolerance allows for Monte Carlo noise
    assert abs(observed - expected) < 5e-6, (
        f"Mean log-return {observed:.8f} too far from expected {expected:.8f}"
    )


# ---------------------------------------------------------------------------
# Async lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_stop_no_task_leak():
    """Provider should start and stop cleanly with no lingering tasks."""
    sim = make_provider(["AAPL"])
    await sim.start()
    await asyncio.sleep(0.6)   # let at least one tick run
    await sim.stop()
    assert sim._task is None


@pytest.mark.asyncio
async def test_start_populates_cache():
    """After start(), get_all_prices() should return non-empty data."""
    sim = make_provider(["AAPL", "TSLA"])
    await sim.start()
    prices = sim.get_all_prices()
    await sim.stop()
    assert "AAPL" in prices
    assert "TSLA" in prices


@pytest.mark.asyncio
async def test_start_stop_multiple_times_is_safe():
    """Calling stop twice should not raise."""
    sim = make_provider(["AAPL"])
    await sim.start()
    await sim.stop()
    await sim.stop()   # second stop should be a no-op


# ---------------------------------------------------------------------------
# Interface conformance
# ---------------------------------------------------------------------------

def test_simulator_is_market_data_provider():
    from backend.market.base import MarketDataProvider
    sim = make_provider()
    assert isinstance(sim, MarketDataProvider)
