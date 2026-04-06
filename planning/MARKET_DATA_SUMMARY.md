# Market Data Backend — Developer Summary

> Reference: `planning/PLAN.md` §6, `planning/MARKET_DATA_REVIEW.md`

---

## What It Does

The market data backend produces a continuously-updated **in-memory price cache** for a set of watched tickers and streams those prices to connected browser clients over Server-Sent Events (SSE).

It has two interchangeable implementations behind one abstract interface:

| Mode | Trigger | Behaviour |
|------|---------|-----------|
| **Simulator** (default) | `MASSIVE_API_KEY` absent/empty | Geometric Brownian Motion, 500 ms ticks, correlated moves, random jump events |
| **Massive API** | `MASSIVE_API_KEY` set | Polls Massive REST API every 15 s, parses live US equity prices |

The rest of the application (SSE route, future portfolio routes, AI chat) is **fully agnostic** to which implementation is running.

---

## Module Map

```
backend/
├── main.py                  Entry point — FastAPI app, lifespan startup/shutdown
├── market/
│   ├── base.py              MarketDataProvider ABC (5 abstract methods)
│   ├── types.py             PriceSnapshot dataclass (the canonical price record)
│   ├── factory.py           create_provider() — reads MASSIVE_API_KEY, returns right impl
│   ├── simulator.py         SimulatorProvider — GBM engine with correlated noise
│   └── massive_provider.py  MassiveProvider — Massive REST API poller
└── routes/
    └── stream.py            GET /api/stream/prices — SSE endpoint
```

### `base.py` — `MarketDataProvider`

Abstract base class. Five methods every provider must implement:

| Method | Signature | Purpose |
|--------|-----------|---------|
| `start` | `async → None` | Begin background loop, seed cache |
| `stop` | `async → None` | Cancel loop, release resources |
| `get_price` | `ticker → PriceSnapshot\|None` | Latest snapshot for one ticker |
| `get_all_prices` | `→ dict[str, PriceSnapshot]` | Shallow copy of full cache |
| `set_watchlist` | `list[str] → None` | Add/evict tickers imperatively |

### `types.py` — `PriceSnapshot`

The single data transfer object flowing through the whole system:

```python
@dataclass
class PriceSnapshot:
    ticker:         str
    price:          float           # latest trade price, rounded to 4dp
    previous_price: float           # price before most recent change
    prev_close:     float           # session open/seed price (static in v1)
    timestamp:      float           # unix epoch seconds
    direction:      "up"|"down"|"flat"
```

### `simulator.py` — `SimulatorProvider`

**GBM formula per tick (dt = 0.5 s):**

```
Z_market ~ N(0,1)                           # one shared market shock
Z_i = β_i · Z_market + √(1−β_i²) · ε_i    # correlated per-ticker noise
S_new = S · exp((μ − σ²/2)·dt + σ·√dt·Z_i)
```

Key constants:

| Constant | Value | Meaning |
|----------|-------|---------|
| `TICK_INTERVAL` | 0.5 s | GBM step size and sleep duration |
| `ANNUAL_DRIFT` | 7% | Shared upward drift across all tickers |
| `EVENT_PROBABILITY` | 0.002 / tick | Sudden ±2–5% jump (~once per 4 min/ticker) |
| `PRICE_FLOOR` | $0.01 | Hard lower bound after any step |

Per-ticker volatility and beta are in `TICKER_PARAMS_ANNUAL`. Unknown tickers get `vol=35%, beta=0.50`.

`prev_close` is set to the seed price at initialisation and **never updated** (v1 limitation — see known limitations below).

### `massive_provider.py` — `MassiveProvider`

- Polls `GET /v2/snapshot/locale/us/markets/stocks/tickers` every 15 seconds
- Re-reads the watchlist from the DB callable on every cycle (dynamic tracking)
- `set_watchlist()` also evicts removed tickers from cache immediately (fast path)
- Skips emitting a cache update if the price hasn't changed (avoids spurious SSE events)
- Falls back gracefully on HTTP errors (logs, continues)
- Accepts both `"tickers"` and `"results"` as the top-level response key

### `factory.py` — `create_provider()`

Single call at startup. Reads `MASSIVE_API_KEY` from the environment:

```python
provider = create_provider(get_watchlist=db.get_watchlist_tickers)
```

### `routes/stream.py` — SSE Endpoint

- **Path:** `GET /api/stream/prices`
- Polls the provider cache at 10 Hz
- Emits a `price` event whenever any ticker's price changes
- Emits a `heartbeat` event every 12 seconds
- Price event payload: `{ticker, price, previous_price, prev_close, timestamp, direction}`
- Detects client disconnect and exits the generator cleanly

---

## Data Flow

```
┌──────────────────────────────────────┐
│  FastAPI lifespan (main.py)          │
│  create_provider() → provider.start()│
└────────────────┬─────────────────────┘
                 │ writes every 500ms (sim) / 15s (massive)
                 ▼
         ┌──────────────┐
         │  price cache  │  dict[ticker, PriceSnapshot]  (in-memory)
         └──────┬───────┘
                │ polled at 10 Hz
                ▼
     ┌─────────────────────┐
     │  stream.py SSE loop  │  emits on change + heartbeat
     └──────────┬──────────┘
                │  text/event-stream
                ▼
         Browser EventSource
```

The watchlist flows in the opposite direction: a callable passed to the provider at construction time lets it re-read the current watchlist from the DB on each cycle. Route handlers will call `provider.set_watchlist()` for immediate effect.

---

## Test Coverage

60 tests across 3 files, all passing.

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_factory.py` | 6 | Provider selection by env var, ABC conformance for both impls |
| `tests/test_simulator.py` | 26 | Seed prices, GBM mechanics, watchlist CRUD, price floor, async lifecycle, correlation mechanism, log-return convergence |
| `tests/test_massive_provider.py` | 28 | Poll loop, watchlist sync, response parsing (both key variants), price extraction fallbacks, error handling, HTTP errors |

Notable test patterns:
- Async lifecycle tests use `@pytest.mark.asyncio` with `asyncio_mode = auto`
- Correlation is tested **deterministically** by patching `random.gauss` to inject a known market shock — no flaky statistics
- `respx` is used to mock the Massive API HTTP calls

---

## Known Limitations (v1)

| # | Location | Limitation |
|---|----------|-----------|
| 1 | `simulator.py:_add_ticker` | `prev_close` is set to the seed price at startup and never updated. "Session change %" on the frontend reflects cumulative drift from seed, not a rolling daily window. Acceptable for v1. |
| 2 | `main.py:_default_watchlist` | Hardcoded watchlist stub — DB integration not yet wired. Replace with `db.get_watchlist_tickers()` once the database layer is implemented. |
| 3 | `routes/stream.py` | No authentication on the SSE endpoint. Fine for v1 (single-user, local deployment). |
| 4 | Massive mode | No retry backoff — transient HTTP errors are logged and the next poll retries after the full `POLL_INTERVAL`. |

---

## What a New Developer Should Know

1. **Add a provider** by subclassing `MarketDataProvider` in `backend/market/` and registering it in `factory.py`. No other files need to change.

2. **Access the provider in routes** via `request.app.state.provider` — it's attached at startup in `main.py`.

3. **The cache is eventually consistent.** The SSE route polls at 10 Hz; there is no push notification from provider to SSE layer. This is intentional (simple, no queues).

4. **Simulator ticks are synchronous** (`_tick()` is a plain method). The async loop just sleeps between calls. This means a very large watchlist could block the event loop for a few milliseconds — not a concern at the expected scale of 10–50 tickers.

5. **Running the backend locally** (outside Docker) requires Python 3.12+ and `uv`:
   ```bash
   cd backend
   uv sync
   uv run uvicorn backend.main:app --reload
   ```

6. **Running tests:**
   ```bash
   python -m pytest          # from repo root
   ```

7. **Demo script** — see `backend/market_data_demo.py` for a live terminal showcase of the simulator with no server required.
