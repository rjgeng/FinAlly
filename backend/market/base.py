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
