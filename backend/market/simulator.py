import asyncio
import math
import random
import time
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .base import MarketDataProvider
from .types import PriceSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

TICK_INTERVAL: float = 0.5           # seconds between GBM steps
EVENT_PROBABILITY: float = 0.002     # per ticker per tick (~once per 4 min)
EVENT_MIN_MOVE: float = 0.02         # minimum jump size (fractional)
EVENT_MAX_MOVE: float = 0.05         # maximum jump size (fractional)
SECONDS_PER_YEAR: float = 252 * 6.5 * 3600   # ~5,896,800 trading seconds/year
ANNUAL_DRIFT: float = 0.07           # mild positive drift (~7% annualised)
PRICE_FLOOR: float = 0.01            # minimum price after any GBM step

# Seed prices for the 10 default watchlist tickers
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

# (annualised_vol, market_beta) per known ticker
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
DEFAULT_ANNUAL_VOL: float = 0.35
DEFAULT_MARKET_BETA: float = 0.50


# ---------------------------------------------------------------------------
# Internal per-ticker state
# ---------------------------------------------------------------------------

@dataclass
class _TickerState:
    current_price: float
    prev_close: float       # session start price (initialised to seed)
    volatility: float       # σ per second (converted from annual)
    drift: float            # μ per second (converted from annual)
    market_beta: float      # loading on shared market factor


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
    Generates synthetic prices using Geometric Brownian Motion with a shared
    market factor for correlated moves and occasional random jump events.

    Each tick (TICK_INTERVAL seconds):
    1. Draw one shared market noise term Z_market ~ N(0,1).
    2. For each tracked ticker, compute a correlated noise term:
           Z_i = beta_i * Z_market + sqrt(1 - beta_i²) * eps_i
       where eps_i ~ N(0,1) is idiosyncratic noise.
    3. Apply the GBM step:
           S_new = S * exp((mu - sigma²/2)*dt + sigma*sqrt(dt)*Z_i)
    4. With probability EVENT_PROBABILITY, apply a sudden ±2–5% jump.
    5. Update the in-memory cache.
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
        self._task = asyncio.create_task(self._tick_loop(), name="simulator-loop")
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
        Sync tracked tickers with the current watchlist.

        New tickers are seeded and added to the cache immediately.
        Removed tickers are evicted from both _states and _cache.
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
        state = _build_ticker_state(ticker)
        self._states[ticker] = state
        # NOTE: prev_close is set to the seed price at initialisation and is never
        # updated, because FinAlly v1 has no concept of market sessions or daily
        # closes. After extended runtime the "session change %" shown on the frontend
        # will reflect cumulative GBM drift from the seed price rather than a true
        # rolling session window. Acceptable for v1 simulation purposes.
        self._cache[ticker] = PriceSnapshot(
            ticker=ticker,
            price=state.current_price,
            previous_price=state.current_price,
            prev_close=state.prev_close,
            timestamp=time.time(),
            direction="flat",
        )

    def _tick(self) -> None:
        """Advance all tickers by one GBM step with correlated market noise."""
        now = time.time()
        dt = TICK_INTERVAL

        # One shared market shock drives correlation across tickers
        z_market = random.gauss(0.0, 1.0)

        for ticker, state in list(self._states.items()):
            prev_price = state.current_price

            # Correlated noise: market factor + idiosyncratic component
            z_idio = random.gauss(0.0, 1.0)
            beta = state.market_beta
            z = beta * z_market + math.sqrt(max(0.0, 1.0 - beta ** 2)) * z_idio

            # GBM step: S_new = S * exp((mu - sigma²/2)*dt + sigma*sqrt(dt)*z)
            mu = state.drift
            sigma = state.volatility
            log_return = (mu - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * z
            new_price = prev_price * math.exp(log_return)

            # Random event injection (sudden ±2–5% jump)
            if random.random() < EVENT_PROBABILITY:
                move = random.uniform(EVENT_MIN_MOVE, EVENT_MAX_MOVE)
                sign = 1 if random.random() < 0.5 else -1
                new_price *= 1.0 + sign * move

            new_price = max(PRICE_FLOOR, new_price)
            state.current_price = new_price

            if new_price > prev_price:
                direction = "up"
            elif new_price < prev_price:
                direction = "down"
            else:
                direction = "flat"

            self._cache[ticker] = PriceSnapshot(
                ticker=ticker,
                price=round(new_price, 4),
                previous_price=round(prev_price, 4),
                prev_close=state.prev_close,
                timestamp=now,
                direction=direction,
            )

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_INTERVAL)
            # Sync watchlist dynamically each cycle so newly added tickers
            # start streaming and removed tickers are evicted immediately.
            self.set_watchlist(self._get_watchlist())
            self._tick()
