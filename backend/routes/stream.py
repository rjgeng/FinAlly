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

POLL_HZ: float = 0.1               # seconds between cache polls (10 Hz)
HEARTBEAT_INTERVAL: float = 12.0   # seconds between heartbeat events


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

        # Emit price events for any tickers whose cached price has changed
        all_prices = provider.get_all_prices()
        for ticker, snap in all_prices.items():
            if last_seen.get(ticker) != snap.price:
                last_seen[ticker] = snap.price
                payload = json.dumps(_snapshot_to_dict(snap))
                yield f"event: price\ndata: {payload}\n\n"

        # Emit a heartbeat on schedule to keep the connection alive
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            last_heartbeat = now
            yield f"event: heartbeat\ndata: {json.dumps({'ts': now})}\n\n"

        await asyncio.sleep(POLL_HZ)


@router.get("/api/stream/prices")
async def stream_prices(request: Request) -> StreamingResponse:
    """
    Server-Sent Events endpoint. Clients should connect with native EventSource.

    Events emitted:
    - ``price``     — whenever a ticker price changes in the cache
    - ``heartbeat`` — every ~12 seconds to keep the connection alive

    Each price event payload:
      {"ticker", "price", "previous_price", "prev_close", "timestamp", "direction"}
    """
    return StreamingResponse(
        _price_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
