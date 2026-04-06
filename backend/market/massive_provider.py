import asyncio
import logging
import time
from collections.abc import Callable

import httpx

from .base import MarketDataProvider
from .types import PriceSnapshot

logger = logging.getLogger(__name__)

POLL_INTERVAL: float = 15.0          # seconds — safe for Massive API free tier
BASE_URL: str = "https://api.massive.com"
SNAPSHOT_PATH: str = "/v2/snapshot/locale/us/markets/stocks/tickers"


class MassiveProvider(MarketDataProvider):
    """
    Polls the Massive REST API for live US equity prices.

    The watchlist is read dynamically from `get_watchlist` on every poll cycle
    so that newly added tickers are picked up without any explicit notification.
    `set_watchlist()` provides an imperative fast-path that also evicts stale
    tickers from the cache immediately when the user removes a ticker via the API.
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

        Called by route handlers when the user adds or removes a ticker so the
        cache reflects the change before the next poll cycle.
        Always pass the full current watchlist, not a delta — both provider
        implementations derive add/remove sets internally.
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
        assert self._client is not None, "MassiveProvider.start() must be called first"
        ticker_str = ",".join(tickers)
        url = f"{BASE_URL}{SNAPSHOT_PATH}"
        params = {"tickers": ticker_str, "apiKey": self._api_key}
        try:
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
        # The v2 snapshot endpoint returns results under the "tickers" key
        results: list[dict] = data.get("tickers") or data.get("results") or []
        now = time.time()
        for raw in results:
            ticker = raw.get("ticker")
            if not ticker:
                continue
            new_price = self._extract_price(raw)
            if new_price is None:
                continue
            prev_close = float((raw.get("prevDay") or {}).get("c") or new_price)

            existing = self._cache.get(ticker)
            if existing is not None and existing.price == new_price:
                # No change — skip so the SSE layer doesn't emit a spurious event
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
                prev_close=round(prev_close, 4),
                timestamp=now,
                direction=direction,
            )

    @staticmethod
    def _extract_price(raw: dict) -> float | None:
        """
        Extract the best available price from a raw Massive API ticker dict.

        Priority:
        1. lastTrade.p  — most recent trade price
        2. day.c        — session close/latest price
        Returns None if neither field is available or parseable.
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
