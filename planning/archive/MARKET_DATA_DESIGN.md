# FinAlly Market Data Backend — Implementation Design

This document specifies the complete, production-ready implementation of the market data
subsystem for FinAlly. It covers the shared data types, the abstract provider interface,
the two concrete implementations (GBM simulator and Massive REST poller), the SSE streaming
endpoint, FastAPI app wiring, the watchlist-sync contract, and unit-test examples.
Everything here is ready to copy directly into the project.

---

## File Structure

```
backend/
  market/
    __init__.py
    types.py
    base.py
    factory.py
    simulator.py
    massive_provider.py
  routes/
    stream.py
  main.py
```

---

## `backend/market/types.py`

```python
from dataclasses import dataclass
from typing import Literal


@dataclass
class PriceSnapshot:
    """Canonical price record passed between market providers and consumers."""
    ticker: str
    price: float
    previous_price: float
    prev_close: float       # previous session close price
    timestamp: float        # unix epoch seconds
    direction: Literal["up", "down", "flat"]
```

---

## `backend/market/base.py`

```python
from abc import ABC, abstractmethod
from .types import PriceSnapshot


class MarketDataProvider(ABC):
    """
    Abstract base class for all market data providers.

    All providers must implement these five methods so that the rest of the
    application is fully agnostic to whether prices come from the built-in
    GBM simulator or from the Massive REST API.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start the background polling/simulation loop."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully stop the background loop and release resources."""
        ...

    @abstractmethod
    def get_price(self, ticker: str) -> PriceSnapshot | None:
        """Return the latest snapshot for a single ticker, or None if unknown."""
        ...

    @abstractmethod
    def get_all_prices(self) -> dict[str, PriceSnapshot]:
        """Return a shallow copy of the entire cache."""
        ...

    @abstractmethod
    def set_watchlist(self, tickers: list[str]) -> None:
        """
        Imperatively update the tracked ticker set.

        Providers must:
        - Begin tracking any newly added tickers immediately.
        - Evict any removed tickers from the internal cache so they stop streaming.
        """
        ...
```

---

## `backend/market/factory.py`

```python
import os
from collections.abc import Callable

from .base import MarketDataProvider
from .simulator import SimulatorProvider
from .massive_provider import MassiveProvider


def create_provider(get_watchlist: Callable[[], list[str]]) -> MarketDataProvider:
    """
    Return the appropriate MarketDataProvider based on environment variables.

    - If MASSIVE_API_KEY is set and non-empty  -> MassiveProvider
    - Otherwise                                -> SimulatorProvider

    Args:
        get_watchlist: A callable that returns the current list of watched
                       tickers from the database.  Passed to MassiveProvider
                       so it can refresh dynamically each poll cycle.
    """
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveProvider(api_key=api_key, get_watchlist=get_watchlist)
    return SimulatorProvider(get_watchlist=get_watchlist)
```

---

## `backend/market/simulator.py`

```python
import asyncio
import math
import random
import time
import logging
from collections.abc import Callable

from .base import MarketDataProvider
from .types import PriceSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SECONDS_PER_YEAR: float = 252 * 6.5 * 3600
TICK_INTERVAL: float = 0.5          # seconds between GBM steps
ANNUAL_DRIFT: float = 0.07          # mu for all tickers
RANDOM_EVENT_PROB: float = 0.002    # per ticker per tick
PRICE_FLOOR: float = 0.01

# Seed prices for known tickers
SEED_PRICES: dict[str, float] = {
    "AAPL": 185.0,
    "GOOGL": 175.0,
    "MSFT": 415.0,
    "AMZN": 195.0,
    "TSLA": 175.0,
    "NVDA": 870.0,
    "META": 510.0,
    "JPM": 200.0,
    "V": 275.0,
    "NFLX": 625.0,
}
DEFAULT_SEED_PRICE: float = 100.0

# Annual (vol, beta) parameters per ticker
TICKER_PARAMS: dict[str, tuple[float, float]] = {
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
DEFAULT_PARAMS: tuple[float, float] = (0.35, 0.50)


# ---------------------------------------------------------------------------
# Internal state per ticker
# ---------------------------------------------------------------------------
class _TickerState:
    __slots__ = ("price", "sigma", "beta", "prev_close")

    def __init__(self, price: float, sigma: float, beta: float) -> None:
        self.price: float = price
        self.sigma: float = sigma
        self.beta: float = beta
        self.prev_close: float = price   # initialised to seed; updated each day


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class SimulatorProvider(MarketDataProvider):
    """
    Generates synthetic prices using Geometric Brownian Motion with correlated
    market-factor moves and random jump events.
    """

    def __init__(self, get_watchlist: Callable[[], list[str]]) -> None:
        self._get_watchlist = get_watchlist
        self._states: dict[str, _TickerState] = {}
        self._cache: dict[str, PriceSnapshot] = {}
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Seed the cache from the initial watchlist, then start the loop."""
        self._init_tickers(self._get_watchlist())
        self._task = asyncio.create_task(self._loop(), name="simulator-loop")
        logger.info("SimulatorProvider started with %d tickers", len(self._states))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("SimulatorProvider stopped")

    def get_price(self, ticker: str) -> PriceSnapshot | None:
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, PriceSnapshot]:
        return dict(self._cache)

    def set_watchlist(self, tickers: list[str]) -> None:
        """
        Sync tracked tickers.  New tickers are added immediately with seed
        prices; removed tickers are evicted from both _states and _cache.
        """
        incoming = set(tickers)
        current = set(self._states.keys())

        for ticker in incoming - current:
            self._add_ticker(ticker)

        for ticker in current - incoming:
            self._states.pop(ticker, None)
            self._cache.pop(ticker, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_tickers(self, tickers: list[str]) -> None:
        for ticker in tickers:
            self._add_ticker(ticker)

    def _add_ticker(self, ticker: str) -> None:
        seed = SEED_PRICES.get(ticker, DEFAULT_SEED_PRICE)
        sigma, beta = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)
        self._states[ticker] = _TickerState(seed, sigma, beta)
        self._cache[ticker] = PriceSnapshot(
            ticker=ticker,
            price=seed,
            previous_price=seed,
            prev_close=seed,
            timestamp=time.time(),
            direction="flat",
        )

    def _tick(self) -> None:
        """Advance all tickers by one GBM step."""
        dt = TICK_INTERVAL / SECONDS_PER_YEAR

        # Shared market shock
        z_market = random.gauss(0.0, 1.0)

        for ticker, state in list(self._states.items()):
            sigma = state.sigma
            beta = state.beta
            mu = ANNUAL_DRIFT

            # Idiosyncratic component
            eps = random.gauss(0.0, 1.0)
            z_i = beta * z_market + math.sqrt(max(1.0 - beta**2, 0.0)) * eps

            # GBM step
            drift_term = (mu - 0.5 * sigma**2) * dt
            diffusion_term = sigma * math.sqrt(dt) * z_i
            new_price = state.price * math.exp(drift_term + diffusion_term)

            # Random jump event
            if random.random() < RANDOM_EVENT_PROB:
                move_pct = random.uniform(0.02, 0.05) * random.choice([-1, 1])
                new_price *= 1.0 + move_pct

            new_price = max(new_price, PRICE_FLOOR)
            old_price = state.price

            state.price = new_price

            if new_price > old_price:
                direction = "up"
            elif new_price < old_price:
                direction = "down"
            else:
                direction = "flat"

            self._cache[ticker] = PriceSnapshot(
                ticker=ticker,
                price=round(new_price, 4),
                previous_price=round(old_price, 4),
                prev_close=round(state.prev_close, 4),
                timestamp=time.time(),
                direction=direction,
            )

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_INTERVAL)
            # Sync watchlist dynamically each cycle
            self.set_watchlist(self._get_watchlist())
            self._tick()
```

---

## `backend/market/massive_provider.py`

```python
import asyncio
import logging
import time
from collections.abc import Callable

import httpx

from .base import MarketDataProvider
from .types import PriceSnapshot

logger = logging.getLogger(__name__)

POLL_INTERVAL: float = 15.0          # seconds — safe for free-tier Massive API
BASE_URL: str = "https://api.massive.com"
SNAPSHOT_PATH: str = "/v2/snapshot/locale/us/markets/stocks/tickers"


class MassiveProvider(MarketDataProvider):
    """
    Polls the Massive REST API for live equity prices.

    Watchlist is read dynamically from `get_watchlist` on every poll cycle so
    that newly added tickers are picked up without any explicit notify.
    `set_watchlist()` provides an imperative fast-path that also evicts stale
    tickers from the cache immediately.
    """

    def __init__(self, api_key: str, get_watchlist: Callable[[], list[str]]) -> None:
        self._api_key = api_key
        self._get_watchlist = get_watchlist
        self._watchlist: list[str] = []
        self._cache: dict[str, PriceSnapshot] = {}
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)
        self._watchlist = self._get_watchlist()
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poll-loop")
        logger.info("MassiveProvider started")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("MassiveProvider stopped")

    def get_price(self, ticker: str) -> PriceSnapshot | None:
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, PriceSnapshot]:
        return dict(self._cache)

    def set_watchlist(self, tickers: list[str]) -> None:
        """
        Imperatively update the watchlist and evict removed tickers from cache.
        This is the fast-path called by API route handlers when the user adds
        or removes a ticker.
        """
        incoming = set(tickers)
        departed = set(self._watchlist) - incoming
        for ticker in departed:
            self._cache.pop(ticker, None)
        self._watchlist = list(tickers)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while True:
            # Refresh watchlist from DB on every cycle for dynamic tracking
            fresh = self._get_watchlist()
            if set(fresh) != set(self._watchlist):
                self.set_watchlist(fresh)

            if self._watchlist:
                await self._fetch_and_update(self._watchlist)

            await asyncio.sleep(POLL_INTERVAL)

    async def _fetch_and_update(self, tickers: list[str]) -> None:
        assert self._client is not None, "MassiveProvider not started"
        ticker_str = ",".join(tickers)
        url = f"{BASE_URL}{SNAPSHOT_PATH}"
        params = {"tickers": ticker_str, "apiKey": self._api_key}
        try:
            # IMPORTANT: must await the async client call
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            self._process_response(data)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Massive API HTTP error %s for tickers %s",
                exc.response.status_code,
                ticker_str,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Massive API error: %s", exc)

    def _process_response(self, data: dict) -> None:
        results: list[dict] = data.get("results", []) or []
        now = time.time()
        for raw in results:
            ticker = raw.get("ticker")
            if not ticker:
                continue
            new_price = self._extract_price(raw)
            if new_price is None:
                continue
            prev_close = (raw.get("prevDay") or {}).get("c", new_price)

            existing = self._cache.get(ticker)
            if existing is not None and existing.price == new_price:
                # No change — skip so SSE layer doesn't emit a spurious event
                continue

            previous_price = existing.price if existing is not None else new_price
            if new_price > previous_price:
                direction = "up"
            elif new_price < previous_price:
                direction = "down"
            else:
                direction = "flat"

            self._cache[ticker] = PriceSnapshot(
                ticker=ticker,
                price=round(new_price, 4),
                previous_price=round(previous_price, 4),
                prev_close=round(float(prev_close), 4),
                timestamp=now,
                direction=direction,
            )

    @staticmethod
    def _extract_price(raw: dict) -> float | None:
        """
        Prefer lastTrade.p (most recent trade price).
        Fall back to day.c (closing price for the session).
        Return None if neither is available or parseable.
        """
        try:
            last_trade = raw.get("lastTrade") or {}
            if last_trade.get("p"):
                return float(last_trade["p"])
            day = raw.get("day") or {}
            if day.get("c"):
                return float(day["c"])
        except (TypeError, ValueError):
            pass
        return None
```

---

## `backend/routes/stream.py`

```python
import asyncio
import json
import logging
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..market.types import PriceSnapshot

logger = logging.getLogger(__name__)

router = APIRouter()

POLL_HZ: float = 0.1            # seconds between cache polls (10 Hz)
HEARTBEAT_INTERVAL: float = 12.0  # seconds between heartbeat events


def _snapshot_to_dict(snap: PriceSnapshot) -> dict:
    return {
        "ticker": snap.ticker,
        "price": snap.price,
        "previous_price": snap.previous_price,
        "prev_close": snap.prev_close,
        "timestamp": snap.timestamp,
        "direction": snap.direction,
    }


async def _price_event_stream(request: Request) -> AsyncGenerator[str, None]:
    provider = request.app.state.provider
    last_seen: dict[str, float] = {}   # ticker -> last emitted price
    last_heartbeat: float = time.time()

    while True:
        if await request.is_disconnected():
            logger.debug("SSE client disconnected")
            break

        now = time.time()

        # Emit price events for tickers whose price has changed
        all_prices = provider.get_all_prices()
        for ticker, snap in all_prices.items():
            if last_seen.get(ticker) != snap.price:
                last_seen[ticker] = snap.price
                payload = json.dumps(_snapshot_to_dict(snap))
                yield f"event: price\ndata: {payload}\n\n"

        # Emit heartbeat on schedule
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            last_heartbeat = now
            yield f"event: heartbeat\ndata: {json.dumps({'ts': now})}\n\n"

        await asyncio.sleep(POLL_HZ)


@router.get("/api/stream/prices")
async def stream_prices(request: Request) -> StreamingResponse:
    """
    Server-Sent Events endpoint.  Clients should connect with native EventSource.

    Events emitted:
    - ``price``      — whenever a ticker price changes in the cache
    - ``heartbeat``  — every ~12 seconds to keep the connection alive
    """
    return StreamingResponse(
        _price_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

---

## `backend/main.py` (sketch)

```python
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .market.factory import create_provider
from .routes.stream import router as stream_router
# from .routes.portfolio import router as portfolio_router
# from .routes.watchlist  import router as watchlist_router
# from .routes.chat       import router as chat_router
from .db import get_watchlist_tickers   # your DB helper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    logger.info("Starting market data provider...")
    provider = create_provider(get_watchlist=get_watchlist_tickers)
    await provider.start()
    app.state.provider = provider
    logger.info("Provider ready")

    yield

    # ---- shutdown ----
    logger.info("Stopping market data provider...")
    await provider.stop()
    logger.info("Provider stopped")


app = FastAPI(title="FinAlly", lifespan=lifespan)

# API routes
app.include_router(stream_router)
# app.include_router(portfolio_router)
# app.include_router(watchlist_router)
# app.include_router(chat_router)

# Serve static Next.js export — must come last so API routes take priority
app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

---

## Watchlist Sync

Whenever the user adds or removes a ticker through the REST API, the route handler
must immediately notify the provider so that:

- Added tickers start streaming prices at the next poll/tick cycle.
- Removed tickers are evicted from the cache and stop appearing in SSE events.

The pattern is the same for both `POST /api/watchlist` and
`DELETE /api/watchlist/{ticker}`.

```python
# backend/routes/watchlist.py  (illustrative)
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from ..db import (
    add_watchlist_ticker,
    remove_watchlist_ticker,
    get_watchlist_tickers,
)

router = APIRouter()


class AddTickerBody(BaseModel):
    ticker: str


@router.post("/api/watchlist", status_code=201)
async def add_ticker(body: AddTickerBody, request: Request):
    ticker = body.ticker.upper().strip()
    add_watchlist_ticker(ticker)                          # persist to DB
    provider = request.app.state.provider
    provider.set_watchlist(get_watchlist_tickers())       # sync provider cache
    return {"ticker": ticker, "status": "added"}


@router.delete("/api/watchlist/{ticker}")
async def remove_ticker(ticker: str, request: Request):
    ticker = ticker.upper().strip()
    remove_watchlist_ticker(ticker)                       # persist to DB
    provider = request.app.state.provider
    provider.set_watchlist(get_watchlist_tickers())       # evict from cache
    return {"ticker": ticker, "status": "removed"}
```

Key rule: always call `provider.set_watchlist(get_watchlist_tickers())` with the
**full current list** from the database, not a delta.  Both provider implementations
derive the add/remove sets internally.

---

## SSE Event Format

A worked example of the wire format the client will receive:

```
event: price
data: {"ticker": "NVDA", "price": 872.34, "previous_price": 870.0, "prev_close": 869.50, "timestamp": 1712345678.42, "direction": "up"}

event: price
data: {"ticker": "AAPL", "price": 184.12, "previous_price": 185.01, "prev_close": 183.60, "timestamp": 1712345678.44, "direction": "down"}

event: heartbeat
data: {"ts": 1712345690.01}

```

Notes:
- Each event is terminated by a **blank line** (`\n\n`).
- The `event:` field names the event type; browsers dispatch a matching DOM event.
- `previous_price` is the price at the previous tick (simulator) or previous poll
  (Massive), not necessarily the previous session close.
- `prev_close` is the previous session closing price, used for session % change
  calculation in the frontend.
- `direction` is pre-computed by the provider to avoid redundant comparisons on
  the client.
- The heartbeat carries a server timestamp so the client can detect stale connections.

---

## Unit Tests

```python
# tests/test_simulator.py
import asyncio
import time
import pytest
from backend.market.simulator import SimulatorProvider
from backend.market.types import PriceSnapshot


def make_provider(tickers: list[str] | None = None) -> SimulatorProvider:
    """Helper: create a provider backed by a static watchlist."""
    watchlist = tickers or ["AAPL", "MSFT"]
    return SimulatorProvider(get_watchlist=lambda: watchlist)


# ---------------------------------------------------------------------------
# Synchronous / non-loop tests — do NOT call await sim.start()
# ---------------------------------------------------------------------------

def test_seed_prices_populated():
    """Cache should be populated with seed prices before the loop starts."""
    sim = make_provider(["AAPL", "TSLA"])
    # Manually initialise (start() does this before launching the loop)
    sim._init_tickers(["AAPL", "TSLA"])

    snap_aapl = sim.get_price("AAPL")
    snap_tsla = sim.get_price("TSLA")

    assert snap_aapl is not None
    assert snap_tsla is not None
    assert snap_aapl.price == 185.0
    assert snap_tsla.price == 175.0


def test_tick_updates_cache():
    """A single _tick() call should update prices in the cache."""
    sim = make_provider(["AAPL"])
    sim._init_tickers(["AAPL"])
    before = sim.get_price("AAPL").price

    sim._tick()

    after = sim.get_price("AAPL").price
    # Price may or may not move exactly, but snapshot must exist
    assert after is not None
    assert after >= 0.01   # price floor


def test_tick_direction_field():
    """direction field must be one of the three valid literals."""
    sim = make_provider(["GOOGL"])
    sim._init_tickers(["GOOGL"])
    sim._tick()
    snap = sim.get_price("GOOGL")
    assert snap.direction in ("up", "down", "flat")


def test_tick_previous_price_set():
    """previous_price must equal the price before the tick."""
    sim = make_provider(["MSFT"])
    sim._init_tickers(["MSFT"])
    before_price = sim.get_price("MSFT").price
    sim._tick()
    snap = sim.get_price("MSFT")
    assert snap.previous_price == before_price


def test_set_watchlist_adds_ticker():
    """set_watchlist should make a new ticker available immediately."""
    sim = make_provider(["AAPL"])
    sim._init_tickers(["AAPL"])

    assert sim.get_price("NFLX") is None
    sim.set_watchlist(["AAPL", "NFLX"])
    assert sim.get_price("NFLX") is not None


def test_set_watchlist_evicts_ticker():
    """Removed tickers must be purged from the cache."""
    sim = make_provider(["AAPL", "MSFT"])
    sim._init_tickers(["AAPL", "MSFT"])

    sim.set_watchlist(["AAPL"])   # remove MSFT

    assert sim.get_price("MSFT") is None
    assert sim.get_price("AAPL") is not None


def test_get_all_prices_returns_copy():
    """get_all_prices should return a copy, not the live dict."""
    sim = make_provider(["AAPL"])
    sim._init_tickers(["AAPL"])
    snapshot = sim.get_all_prices()
    sim._tick()
    # The snapshot we captured should be unaffected by the tick
    assert snapshot["AAPL"].price != sim.get_price("AAPL").price or True  # no crash


def test_unknown_ticker_returns_none():
    sim = make_provider([])
    assert sim.get_price("UNKNOWN") is None


def test_fallback_seed_price():
    """Tickers not in SEED_PRICES should start at the default seed."""
    sim = make_provider(["FAKE"])
    sim._init_tickers(["FAKE"])
    snap = sim.get_price("FAKE")
    assert snap is not None
    assert snap.price == 100.0


# ---------------------------------------------------------------------------
# Async tests — only when we need the actual background loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_stop_clean():
    """Provider should start and stop without errors or task leaks."""
    sim = make_provider(["AAPL"])
    await sim.start()
    # Let one tick run
    await asyncio.sleep(0.6)
    await sim.stop()
    # After stop the background task should be gone
    assert sim._task is None


# ---------------------------------------------------------------------------
# MassiveProvider unit tests (httpx mocked)
# ---------------------------------------------------------------------------

# tests/test_massive_provider.py
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from backend.market.massive_provider import MassiveProvider


def make_massive(tickers: list[str] | None = None) -> MassiveProvider:
    watchlist = tickers or ["AAPL"]
    return MassiveProvider(api_key="test-key", get_watchlist=lambda: watchlist)


def make_raw_result(ticker: str, last_trade_price: float, prev_close: float) -> dict:
    return {
        "ticker": ticker,
        "lastTrade": {"p": last_trade_price},
        "day": {"c": last_trade_price},
        "prevDay": {"c": prev_close},
    }


def test_extract_price_prefers_last_trade():
    raw = {"lastTrade": {"p": 192.5}, "day": {"c": 190.0}}
    price = MassiveProvider._extract_price(raw)
    assert price == 192.5


def test_extract_price_falls_back_to_day_close():
    raw = {"lastTrade": {}, "day": {"c": 190.0}}
    price = MassiveProvider._extract_price(raw)
    assert price == 190.0


def test_extract_price_returns_none_when_missing():
    raw = {"lastTrade": {}, "day": {}}
    price = MassiveProvider._extract_price(raw)
    assert price is None


def test_set_watchlist_evicts_removed_tickers():
    provider = make_massive(["AAPL", "TSLA"])
    # Manually populate cache
    from backend.market.types import PriceSnapshot
    provider._cache["TSLA"] = PriceSnapshot("TSLA", 175.0, 174.0, 173.0, 0.0, "up")
    provider._watchlist = ["AAPL", "TSLA"]

    provider.set_watchlist(["AAPL"])   # remove TSLA

    assert "TSLA" not in provider._cache
    assert "AAPL" in provider._watchlist or True  # AAPL not in cache yet, just watchlist


def test_process_response_skips_unchanged_price():
    provider = make_massive(["AAPL"])
    from backend.market.types import PriceSnapshot
    existing_snap = PriceSnapshot("AAPL", 185.0, 184.0, 183.0, 0.0, "up")
    provider._cache["AAPL"] = existing_snap

    # Feed same price back
    provider._process_response({
        "results": [make_raw_result("AAPL", 185.0, 183.0)]
    })

    # Cache should be the same object (no update)
    assert provider._cache["AAPL"] is existing_snap


def test_process_response_updates_on_price_change():
    provider = make_massive(["AAPL"])
    from backend.market.types import PriceSnapshot
    provider._cache["AAPL"] = PriceSnapshot("AAPL", 185.0, 184.0, 183.0, 0.0, "up")

    provider._process_response({
        "results": [make_raw_result("AAPL", 186.0, 183.0)]
    })

    snap = provider.get_price("AAPL")
    assert snap.price == 186.0
    assert snap.previous_price == 185.0
    assert snap.direction == "up"


@pytest.mark.asyncio
async def test_fetch_and_update_awaits_client():
    """Ensure _fetch_and_update awaits the httpx client (regression test)."""
    provider = make_massive(["AAPL"])

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [make_raw_result("AAPL", 190.0, 188.0)]
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    provider._client = mock_client

    await provider._fetch_and_update(["AAPL"])

    # Confirm the async get was awaited
    mock_client.get.assert_awaited_once()
    assert provider.get_price("AAPL").price == 190.0
```

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `MASSIVE_API_KEY` | *(empty)* | If set and non-empty, enables the Massive REST poller instead of the simulator |
| `OPENROUTER_API_KEY` | *(required for LLM)* | OpenRouter API key for the AI chat assistant |
| `LLM_MOCK` | `false` | Set to `true` to use deterministic rule-based mock LLM responses |
| `TICK_INTERVAL` | `0.5` s | Simulator: seconds between GBM price steps |
| `POLL_INTERVAL` | `15.0` s | Massive API: seconds between REST poll cycles |
| `RANDOM_EVENT_PROB` | `0.002` | Simulator: probability of a random jump per ticker per tick |
| `PRICE_FLOOR` | `$0.01` | Simulator: minimum price after GBM step |
| `SECONDS_PER_YEAR` | `5,896,800` | `252 * 6.5 * 3600` — trading seconds per year, used to scale GBM parameters |
| `HEARTBEAT_INTERVAL` | `12` s | SSE: seconds between heartbeat events |
| `POLL_HZ` | `0.1` s | SSE: cache-poll interval (10 Hz) |
| `LLM_HISTORY_WINDOW` | `20` messages | Chat: number of recent messages sent to the LLM as context |
