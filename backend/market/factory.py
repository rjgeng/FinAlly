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
                       tickers from the database. Passed to providers so they
                       can refresh the watchlist dynamically on each cycle.
    """
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveProvider(api_key=api_key, get_watchlist=get_watchlist)
    return SimulatorProvider(get_watchlist=get_watchlist)
