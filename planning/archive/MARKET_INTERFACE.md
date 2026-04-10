# Market Data Interface — Unified Python Design

This document defines the shared Python interface for market data in FinAlly, and how the backend selects between the Massive API provider and the built-in simulator.

---

## Design Goals

- All downstream code (SSE streaming, API routes, portfolio snapshots) is **agnostic to the data source**.
- The selection between live data and simulator is determined once at startup via the `MASSIVE_API_KEY` environment variable.
- Both providers expose identical method signatures and return the same data types.
- The interface is async-first; providers manage their own background polling/simulation loops.

---

## Shared Data Types

```python
# backend/market/types.py

from dataclasses import dataclass
from typing import Literal


@dataclass
class PriceSnapshot:
    ticker: str
    price: float               # Current price (last trade or simulated)
    previous_price: float      # Price from the immediately preceding update
    prev_close: float          # Previous session close (for day change %)
    timestamp: float           # Unix timestamp (seconds) of this snapshot
    direction: Literal["up", "down", "flat"]  # Compared to previous_price
```

`PriceSnapshot` is the single data contract between providers and consumers. It is also what gets serialised into SSE events.

---

## Abstract Interface

```python
# backend/market/base.py

from abc import ABC, abstractmethod
from .types import PriceSnapshot


class MarketDataProvider(ABC):
    """Common interface for all market data sources."""

    @abstractmethod
    async def start(self) -> None:
        """Start background data collection (polling or simulation loop)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully stop background activity."""
        ...

    @abstractmethod
    def get_price(self, ticker: str) -> PriceSnapshot | None:
        """Return the latest snapshot for a single ticker, or None if unknown."""
        ...

    @abstractmethod
    def get_all_prices(self) -> dict[str, PriceSnapshot]:
        """Return the latest snapshots for all currently watched tickers."""
        ...

    @abstractmethod
    def set_watchlist(self, tickers: list[str]) -> None:
        """Update the set of tickers to track.

        Called whenever the user's watchlist changes. Providers should
        begin tracking new tickers on the next poll/tick cycle.
        """
        ...
```

---

## Provider Selection at Startup

```python
# backend/market/factory.py

import os
from .base import MarketDataProvider
from .massive_provider import MassiveProvider
from .simulator import SimulatorProvider


def create_market_provider(initial_tickers: list[str]) -> MarketDataProvider:
    """Instantiate the correct provider based on environment configuration."""
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveProvider(api_key=api_key, initial_tickers=initial_tickers)
    return SimulatorProvider(initial_tickers=initial_tickers)
```

---

## Massive API Provider

```python
# backend/market/massive_provider.py

import asyncio
import time
import httpx
from .base import MarketDataProvider
from .types import PriceSnapshot


_BASE_URL = "https://api.massive.com"
_POLL_INTERVAL_SECONDS = 15  # Free tier safe; reduce to 2–5 on paid tiers


class MassiveProvider(MarketDataProvider):
    def __init__(self, api_key: str, initial_tickers: list[str]) -> None:
        self._api_key = api_key
        self._watchlist: list[str] = list(initial_tickers)
        self._cache: dict[str, PriceSnapshot] = {}
        self._task: asyncio.Task | None = None

    # --- Lifecycle ---

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

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
        self._watchlist = list(tickers)

    # --- Internal ---

    async def _poll_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                tickers = list(self._watchlist)  # snapshot; watchlist may change
                if tickers:
                    try:
                        await self._fetch_and_update(client, tickers)
                    except Exception as exc:
                        # Log and continue; don't crash the loop
                        print(f"[MassiveProvider] poll error: {exc}")
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _fetch_and_update(
        self, client: httpx.AsyncClient, tickers: list[str]
    ) -> None:
        url = f"{_BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers"
        params = {"tickers": ",".join(tickers), "apiKey": self._api_key}
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        now = time.time()
        for raw in data.get("tickers", []):
            ticker = raw["ticker"]
            new_price = self._extract_price(raw)
            if new_price is None:
                continue

            prev = self._cache.get(ticker)
            previous_price = prev.price if prev else new_price
            prev_close = (raw.get("prevDay") or {}).get("c", new_price)

            direction: str
            if new_price > previous_price:
                direction = "up"
            elif new_price < previous_price:
                direction = "down"
            else:
                direction = "flat"

            # Only update the cache if the price has actually changed, so that
            # the SSE layer can use cache changes as the trigger to emit events.
            if prev is None or new_price != prev.price:
                self._cache[ticker] = PriceSnapshot(
                    ticker=ticker,
                    price=new_price,
                    previous_price=previous_price,
                    prev_close=prev_close,
                    timestamp=now,
                    direction=direction,
                )

    @staticmethod
    def _extract_price(raw: dict) -> float | None:
        """Best-effort price extraction: last trade → day close → None."""
        last_trade = raw.get("lastTrade") or {}
        if last_trade.get("p"):
            return float(last_trade["p"])
        day = raw.get("day") or {}
        if day.get("c"):
            return float(day["c"])
        return None
```

---

## Simulator Provider

The `SimulatorProvider` implements the same interface. See `MARKET_SIMULATOR.md` for its design and full implementation.

```python
# backend/market/simulator.py  (see MARKET_SIMULATOR.md for full implementation)

from .base import MarketDataProvider

class SimulatorProvider(MarketDataProvider):
    """GBM-based price simulator. See MARKET_SIMULATOR.md."""
    ...
```

---

## Price Cache and SSE Integration

The SSE endpoint reads from the provider's in-memory cache. Only changed prices are emitted as events, keeping the stream event-driven rather than a constant rebroadcast.

```python
# backend/routes/stream.py  (sketch)

import asyncio
from fastapi import Request
from fastapi.responses import StreamingResponse
from ..market.base import MarketDataProvider


async def price_stream(request: Request, provider: MarketDataProvider):
    async def event_generator():
        last_seen: dict[str, float] = {}
        heartbeat_interval = 12  # seconds
        last_heartbeat = 0.0

        while not await request.is_disconnected():
            now = asyncio.get_event_loop().time()

            for ticker, snap in provider.get_all_prices().items():
                if last_seen.get(ticker) != snap.price:
                    last_seen[ticker] = snap.price
                    yield _format_price_event(snap)

            if now - last_heartbeat >= heartbeat_interval:
                yield "event: heartbeat\ndata: {}\n\n"
                last_heartbeat = now

            await asyncio.sleep(0.1)  # poll cache at ~10 Hz

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _format_price_event(snap) -> str:
    import json
    payload = {
        "ticker": snap.ticker,
        "price": snap.price,
        "previous_price": snap.previous_price,
        "prev_close": snap.prev_close,
        "timestamp": snap.timestamp,
        "direction": snap.direction,
    }
    return f"event: price\ndata: {json.dumps(payload)}\n\n"
```

---

## Lifecycle Integration (FastAPI lifespan)

```python
# backend/main.py  (sketch)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from .market.factory import create_market_provider
from .db import get_initial_watchlist

provider: MarketDataProvider | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global provider
    tickers = get_initial_watchlist()          # reads from SQLite
    provider = create_market_provider(tickers)
    await provider.start()
    yield
    await provider.stop()


app = FastAPI(lifespan=lifespan)
```

---

## Watchlist Sync

When a ticker is added or removed via the watchlist API, the provider's watchlist is updated immediately so it is included in the next poll/tick cycle:

```python
# In the watchlist POST/DELETE route handler:
provider.set_watchlist(db.get_watchlist_tickers(user_id="default"))
```

---

## Summary

| Aspect                   | Detail                                                       |
|--------------------------|--------------------------------------------------------------|
| Interface location       | `backend/market/base.py`                                     |
| Shared type              | `PriceSnapshot` in `backend/market/types.py`                 |
| Factory                  | `backend/market/factory.py` — reads `MASSIVE_API_KEY`        |
| Massive provider         | `backend/market/massive_provider.py`                         |
| Simulator provider       | `backend/market/simulator.py`                                |
| Selection logic          | `MASSIVE_API_KEY` set → Massive; absent/empty → Simulator    |
| Polling interval (Massive)| 15 s (free tier); reduce to 2–5 s on paid plans             |
| Simulator tick interval  | ~500 ms                                                      |
| SSE polling rate          | Cache polled at ~10 Hz; events emitted only on price change  |
