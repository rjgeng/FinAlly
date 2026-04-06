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
