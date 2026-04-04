# Market Simulator — Design and Code Structure

This document describes the design and implementation of `SimulatorProvider`, the default market data source used when `MASSIVE_API_KEY` is not set.

---

## Design Goals

- Produce plausible, continuously moving prices that make the UI feel live.
- Implement the same `MarketDataProvider` interface as `MassiveProvider` — no downstream code should know which is running.
- Support any set of tickers, including dynamically added ones.
- Generate correlated moves across related tickers so the simulation feels coherent.
- Occasionally inject "events" (sudden 2–5% moves) to create interesting visual moments.

---

## Price Model: Geometric Brownian Motion (GBM)

Prices evolve as a discrete GBM step every 500 ms:

```
S(t+Δt) = S(t) · exp((μ - σ²/2)·Δt + σ·√Δt·Z)
```

Where:
- `S(t)` — current price
- `μ` — drift per second (annualised drift / seconds_per_year, typically small)
- `σ` — volatility per second (annualised vol / √seconds_per_year)
- `Δt` — tick interval in seconds (0.5 s)
- `Z` — standard normal random variable

This formulation guarantees prices stay positive and follow a log-normal distribution — consistent with the Black-Scholes assumption.

### Volatility calibration

Mapping from common annualised vol to per-second σ:
```
σ_per_sec = σ_annual / sqrt(252 * 6.5 * 3600)
           = σ_annual / sqrt(5_896_800)
           ≈ σ_annual / 2428
```

For a "high-vol" ticker like TSLA (σ_annual ≈ 0.55): σ_per_sec ≈ 0.000226.
For a "low-vol" ticker like JPM (σ_annual ≈ 0.25): σ_per_sec ≈ 0.000103.

---

## Correlated Moves

Tickers in related sectors move with positive correlation. This is achieved with a simple two-factor model:

```
Z_i = ρ_market · Z_market + sqrt(1 - ρ²_market) · ε_i
```

Where:
- `Z_market` — a single shared market noise term drawn once per tick
- `ε_i` — idiosyncratic noise term for ticker `i`
- `ρ_market` — ticker's loading on the market factor (0.3–0.7 typical)

This is computationally trivial (no matrix inversion) and produces realistic co-movement.

---

## Random Events

Every tick, each ticker has a small probability of a sudden price jump:

- Event probability per tick: 0.002 (≈ once every ~4 minutes per ticker)
- Move size: drawn from `Uniform(0.02, 0.05)` with random sign
- Applied as a multiplier: `S_new = S_current · (1 ± event_size)`

Events are independent per ticker.

---

## Seed Prices

The simulator starts from realistic reference prices. These are used to initialise `current_price` and `prev_close`:

```python
SEED_PRICES: dict[str, float] = {
    "AAPL":  185.0,
    "GOOGL": 175.0,
    "MSFT":  415.0,
    "AMZN":  195.0,
    "TSLA":  175.0,
    "NVDA":  870.0,
    "META":  510.0,
    "JPM":   200.0,
    "V":     275.0,
    "NFLX":  625.0,
}

DEFAULT_SEED_PRICE = 100.0  # fallback for dynamically added tickers
```

---

## Per-Ticker Parameters

```python
from dataclasses import dataclass, field

@dataclass
class TickerParams:
    drift: float          # μ per second (small positive, ~0.00002)
    volatility: float     # σ per second
    market_beta: float    # ρ_market loading (0.3–0.7)
```

Default parameter map for the 10 seed tickers (annualised values used to compute per-second σ):

```python
# (annualised_vol, market_beta)
TICKER_PARAMS_ANNUAL: dict[str, tuple[float, float]] = {
    "AAPL":  (0.30, 0.65),
    "GOOGL": (0.32, 0.60),
    "MSFT":  (0.28, 0.65),
    "AMZN":  (0.35, 0.55),
    "TSLA":  (0.55, 0.50),
    "NVDA":  (0.50, 0.55),
    "META":  (0.40, 0.55),
    "JPM":   (0.25, 0.60),
    "V":     (0.22, 0.55),
    "NFLX":  (0.42, 0.45),
}

DEFAULT_ANNUAL_VOL = 0.35
DEFAULT_MARKET_BETA = 0.50
```

---

## Full Implementation

```python
# backend/market/simulator.py

import asyncio
import math
import random
import time
from dataclasses import dataclass

from .base import MarketDataProvider
from .types import PriceSnapshot


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

TICK_INTERVAL = 0.5          # seconds between price updates
EVENT_PROBABILITY = 0.002    # per ticker per tick
EVENT_MIN_MOVE = 0.02        # minimum event size (fractional)
EVENT_MAX_MOVE = 0.05        # maximum event size (fractional)
SECONDS_PER_YEAR = 252 * 6.5 * 3600  # ~5,896,800

SEED_PRICES: dict[str, float] = {
    "AAPL":  185.0,
    "GOOGL": 175.0,
    "MSFT":  415.0,
    "AMZN":  195.0,
    "TSLA":  175.0,
    "NVDA":  870.0,
    "META":  510.0,
    "JPM":   200.0,
    "V":     275.0,
    "NFLX":  625.0,
}
DEFAULT_SEED_PRICE = 100.0

# (annualised_vol, market_beta)
TICKER_PARAMS_ANNUAL: dict[str, tuple[float, float]] = {
    "AAPL":  (0.30, 0.65),
    "GOOGL": (0.32, 0.60),
    "MSFT":  (0.28, 0.65),
    "AMZN":  (0.35, 0.55),
    "TSLA":  (0.55, 0.50),
    "NVDA":  (0.50, 0.55),
    "META":  (0.40, 0.55),
    "JPM":   (0.25, 0.60),
    "V":     (0.22, 0.55),
    "NFLX":  (0.42, 0.45),
}
DEFAULT_ANNUAL_VOL = 0.35
DEFAULT_MARKET_BETA = 0.50
ANNUAL_DRIFT = 0.07  # mild positive drift (~7% annual) for all tickers


# ---------------------------------------------------------------------------
# Internal state per ticker
# ---------------------------------------------------------------------------

@dataclass
class _TickerState:
    current_price: float
    prev_close: float        # session start price; fixed until next session
    volatility: float        # σ per second
    drift: float             # μ per second
    market_beta: float       # correlation to market factor


def _build_ticker_state(ticker: str) -> _TickerState:
    seed = SEED_PRICES.get(ticker, DEFAULT_SEED_PRICE)
    annual_vol, beta = TICKER_PARAMS_ANNUAL.get(
        ticker, (DEFAULT_ANNUAL_VOL, DEFAULT_MARKET_BETA)
    )
    vol_per_sec = annual_vol / math.sqrt(SECONDS_PER_YEAR)
    drift_per_sec = ANNUAL_DRIFT / SECONDS_PER_YEAR
    return _TickerState(
        current_price=seed,
        prev_close=seed,
        volatility=vol_per_sec,
        drift=drift_per_sec,
        market_beta=beta,
    )


# ---------------------------------------------------------------------------
# SimulatorProvider
# ---------------------------------------------------------------------------

class SimulatorProvider(MarketDataProvider):
    """
    GBM-based stock price simulator.

    Each tick (TICK_INTERVAL seconds):
    1. Draw one shared market noise term Z_market ~ N(0,1).
    2. For each tracked ticker, compute a correlated noise term:
           Z_i = beta_i * Z_market + sqrt(1 - beta_i²) * eps_i
       where eps_i ~ N(0,1) is idiosyncratic noise.
    3. Apply the GBM step:
           S_new = S * exp((mu - sigma²/2)*dt + sigma*sqrt(dt)*Z_i)
    4. With probability EVENT_PROBABILITY, apply a sudden ±2–5% jump.
    5. Update the in-memory cache; only changed prices trigger SSE events.
    """

    def __init__(self, initial_tickers: list[str]) -> None:
        self._states: dict[str, _TickerState] = {
            t: _build_ticker_state(t) for t in initial_tickers
        }
        self._cache: dict[str, PriceSnapshot] = {}
        self._task: asyncio.Task | None = None
        self._dt = TICK_INTERVAL

    # --- Lifecycle ---

    async def start(self) -> None:
        # Populate cache with seed prices before the loop starts so that
        # get_all_prices() returns data immediately.
        now = time.time()
        for ticker, state in self._states.items():
            self._cache[ticker] = PriceSnapshot(
                ticker=ticker,
                price=state.current_price,
                previous_price=state.current_price,
                prev_close=state.prev_close,
                timestamp=now,
                direction="flat",
            )
        self._task = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # --- Interface ---

    def get_price(self, ticker: str) -> PriceSnapshot | None:
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, PriceSnapshot]:
        return dict(self._cache)

    def set_watchlist(self, tickers: list[str]) -> None:
        incoming = set(tickers)
        existing = set(self._states.keys())

        for ticker in incoming - existing:
            state = _build_ticker_state(ticker)
            self._states[ticker] = state
            # Immediately populate cache so new tickers appear without delay
            now = time.time()
            self._cache[ticker] = PriceSnapshot(
                ticker=ticker,
                price=state.current_price,
                previous_price=state.current_price,
                prev_close=state.prev_close,
                timestamp=now,
                direction="flat",
            )

        for ticker in existing - incoming:
            self._states.pop(ticker, None)
            self._cache.pop(ticker, None)

    # --- Simulation loop ---

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(self._dt)
            self._tick()

    def _tick(self) -> None:
        now = time.time()
        dt = self._dt

        # Shared market factor for correlated moves
        z_market = random.gauss(0.0, 1.0)

        for ticker, state in list(self._states.items()):
            prev_price = state.current_price

            # Correlated noise
            z_idio = random.gauss(0.0, 1.0)
            z = (
                state.market_beta * z_market
                + math.sqrt(max(0.0, 1.0 - state.market_beta ** 2)) * z_idio
            )

            # GBM step
            mu = state.drift
            sigma = state.volatility
            log_return = (mu - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * z
            new_price = prev_price * math.exp(log_return)

            # Random event injection
            if random.random() < EVENT_PROBABILITY:
                move = random.uniform(EVENT_MIN_MOVE, EVENT_MAX_MOVE)
                direction = 1 if random.random() < 0.5 else -1
                new_price *= 1.0 + direction * move

            # Floor at 1 cent to avoid degenerate prices
            new_price = max(0.01, new_price)
            state.current_price = new_price

            # Determine direction vs previous snapshot
            snap_direction: str
            if new_price > prev_price:
                snap_direction = "up"
            elif new_price < prev_price:
                snap_direction = "down"
            else:
                snap_direction = "flat"

            self._cache[ticker] = PriceSnapshot(
                ticker=ticker,
                price=round(new_price, 4),
                previous_price=round(prev_price, 4),
                prev_close=state.prev_close,
                timestamp=now,
                direction=snap_direction,
            )
```

---

## Testing the Simulator

Unit tests should verify:

1. **Prices stay positive** — run 10,000 ticks on a ticker with high volatility (σ_annual=1.0); assert all prices > 0.
2. **GBM drift** — over many ticks, mean log-return converges to `(μ - σ²/2)·Δt`.
3. **Events fire** — inject a fixed random seed; assert at least one event occurs in 10,000 ticks.
4. **Correlation** — over 1,000 ticks, correlation between two high-beta tickers is meaningfully positive (> 0.2).
5. **set_watchlist** — adding a ticker immediately populates the cache; removing it clears it.
6. **Interface conformance** — `SimulatorProvider` implements all `MarketDataProvider` methods (duck-type check or isinstance with ABC).

```python
# test/unit/test_simulator.py  (example)

import asyncio
import math
from backend.market.simulator import SimulatorProvider


def test_prices_stay_positive():
    sim = SimulatorProvider(["TSLA"])
    asyncio.get_event_loop().run_until_complete(sim.start())
    for _ in range(10_000):
        sim._tick()
    price = sim.get_price("TSLA").price
    assert price > 0


def test_add_ticker_populates_cache():
    sim = SimulatorProvider(["AAPL"])
    asyncio.get_event_loop().run_until_complete(sim.start())
    sim.set_watchlist(["AAPL", "PYPL"])
    assert sim.get_price("PYPL") is not None


def test_remove_ticker_clears_cache():
    sim = SimulatorProvider(["AAPL", "TSLA"])
    asyncio.get_event_loop().run_until_complete(sim.start())
    sim.set_watchlist(["AAPL"])
    assert sim.get_price("TSLA") is None
```

---

## Summary

| Property              | Value                                       |
|-----------------------|---------------------------------------------|
| Tick interval         | 500 ms                                      |
| Price model           | Discrete GBM                                |
| Correlation model     | Single market factor (beta loading)         |
| Event probability     | 0.2% per ticker per tick                    |
| Event move size       | 2–5% (random sign)                          |
| Drift                 | ~7% annualised (mild upward bias)           |
| Floor                 | $0.01 per share                             |
| Dynamic tickers       | Yes — `set_watchlist()` adds/removes live   |
| Seed prices           | Hardcoded realistic values for 10 defaults  |
| Fallback seed price   | $100.00 for any unknown ticker              |
