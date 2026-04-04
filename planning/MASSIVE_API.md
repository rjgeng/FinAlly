# Massive API — Stock Price Data Reference

Massive (formerly Polygon.io) provides a REST API for real-time and delayed US stock market data. This document covers the endpoints relevant to FinAlly: fetching current prices for a watchlist of tickers.

**Base URL:** `https://api.massive.com`
(Legacy alias `https://api.polygon.io` continues to work.)

**Authentication:** Append `?apiKey=<MASSIVE_API_KEY>` to every request, or pass it as the `Authorization: Bearer <key>` header.

**Rate limits:**
- Free tier: 5 requests/minute, end-of-day data only (15-minute delay)
- Paid tiers: unlimited requests (stay under ~100 req/s to avoid soft throttling), real-time data

**Snapshot reset:** Daily snapshot data clears at 3:30 AM EST and repopulates from ~4:00 AM EST as exchanges open.

---

## Key Endpoints

### 1. Multi-Ticker Snapshot (Primary endpoint for FinAlly)

Fetch current prices for up to all tickers in one call. Pass a comma-separated list to filter.

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,TSLA,NVDA&apiKey=KEY
```

**Query parameters:**

| Parameter | Type   | Description                                                           |
|-----------|--------|-----------------------------------------------------------------------|
| `tickers` | string | Comma-separated list of case-sensitive ticker symbols (e.g. `AAPL,TSLA`). Omit for all tickers. |
| `apiKey`  | string | Your API key                                                          |

**Response schema:**

```json
{
  "status": "OK",
  "count": 2,
  "tickers": [
    {
      "ticker": "AAPL",
      "todaysChange": 1.42,
      "todaysChangePerc": 0.73,
      "updated": 1712345678000000000,
      "day": {
        "o": 194.20,
        "h": 196.80,
        "l": 193.50,
        "c": 195.60,
        "v": 52341200,
        "vw": 195.12
      },
      "lastTrade": {
        "p": 195.60,
        "s": 100,
        "t": 1712345678000000000
      },
      "lastQuote": {
        "P": 195.61,
        "S": 2,
        "p": 195.59,
        "s": 3,
        "t": 1712345678500000000
      },
      "min": {
        "o": 195.40,
        "h": 195.70,
        "l": 195.30,
        "c": 195.60,
        "v": 12400,
        "vw": 195.52,
        "t": 1712345640000
      },
      "prevDay": {
        "o": 192.50,
        "h": 195.00,
        "l": 191.80,
        "c": 194.18,
        "v": 48200000,
        "vw": 193.42
      }
    }
  ]
}
```

**Key fields:**

| Field                | Description                                          |
|----------------------|------------------------------------------------------|
| `ticker`             | Ticker symbol                                        |
| `todaysChange`       | Dollar change vs. previous close                     |
| `todaysChangePerc`   | Percentage change vs. previous close                 |
| `updated`            | Nanosecond Unix timestamp of last update             |
| `day.c`              | Current session close/latest price                  |
| `day.o`, `h`, `l`    | Session open, high, low                              |
| `day.v`              | Session volume                                       |
| `lastTrade.p`        | Price of the last trade                              |
| `lastTrade.t`        | Nanosecond timestamp of last trade                   |
| `prevDay.c`          | Previous trading day close                           |

**Python example (raw `httpx`):**

```python
import httpx

BASE_URL = "https://api.massive.com"

async def fetch_snapshots(tickers: list[str], api_key: str) -> list[dict]:
    """Fetch current price snapshots for a list of tickers."""
    url = f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers"
    params = {
        "tickers": ",".join(tickers),
        "apiKey": api_key,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    return data.get("tickers", [])
```

---

### 2. Unified Snapshot (Alternative — up to 250 tickers, cross-asset)

```
GET /v3/snapshot?ticker.any_of=AAPL,TSLA,NVDA&limit=250&apiKey=KEY
```

**Query parameters:**

| Parameter        | Type    | Description                                                      |
|------------------|---------|------------------------------------------------------------------|
| `ticker.any_of`  | string  | Comma-separated tickers (max 250)                                |
| `limit`          | integer | Results per page, max 250 (default 10)                           |
| `order`          | string  | `asc` or `desc`                                                  |
| `sort`           | string  | Field to sort by (e.g. `ticker`)                                 |
| `apiKey`         | string  | Your API key                                                     |

**Response schema:**

```json
{
  "request_id": "abc123",
  "results": [
    {
      "ticker": "AAPL",
      "type": "CS",
      "market_status": "open",
      "session": {
        "open": 194.20,
        "high": 196.80,
        "low": 193.50,
        "close": 195.60,
        "volume": 52341200,
        "change": 1.42,
        "change_percent": 0.73,
        "previous_close": 194.18
      },
      "last_trade": {
        "price": 195.60,
        "size": 100,
        "timestamp": 1712345678000000000
      },
      "last_quote": {
        "ask": 195.61,
        "ask_size": 200,
        "bid": 195.59,
        "bid_size": 300,
        "timestamp": 1712345678500000000
      },
      "fmv": 195.58
    }
  ],
  "next_url": "https://api.massive.com/v3/snapshot?cursor=abc..."
}
```

**Notes:**
- `fmv` (Fair Market Value) is only available on Business plans.
- Use `next_url` cursor for pagination if you have more than 250 tickers.

**Python example:**

```python
import httpx

BASE_URL = "https://api.massive.com"

async def fetch_unified_snapshots(tickers: list[str], api_key: str) -> list[dict]:
    """Fetch unified snapshots for up to 250 tickers."""
    url = f"{BASE_URL}/v3/snapshot"
    params = {
        "ticker.any_of": ",".join(tickers),
        "limit": 250,
        "apiKey": api_key,
    }
    results = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        while url:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            results.extend(data.get("results", []))
            url = data.get("next_url")
            params = {"apiKey": api_key}  # cursor already encoded in next_url
    return results
```

---

### 3. Single Ticker Snapshot

```
GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey=KEY
```

Returns the same structure as the multi-ticker endpoint but for one ticker. Useful for on-demand price checks.

**Python example:**

```python
async def fetch_single_snapshot(ticker: str, api_key: str) -> dict | None:
    url = f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params={"apiKey": api_key})
        response.raise_for_status()
        data = response.json()
    return data.get("ticker")
```

---

### 4. Last Trade (single ticker)

```
GET /v2/last/trade/{ticker}?apiKey=KEY
```

**Response:**

```json
{
  "status": "OK",
  "request_id": "f05562305bd26ced64b98ed68b3c5d96",
  "results": {
    "T": "AAPL",
    "p": 195.60,
    "s": 100,
    "t": 1712345678000000000,
    "y": 1712345677968000000
  }
}
```

| Field | Description              |
|-------|--------------------------|
| `p`   | Trade price              |
| `s`   | Trade size (shares)      |
| `t`   | SIP timestamp (ns)       |
| `y`   | Exchange timestamp (ns)  |

---

### 5. Previous Day Bar

```
GET /v2/aggs/ticker/{ticker}/prev?apiKey=KEY
```

**Response:**

```json
{
  "status": "OK",
  "resultsCount": 1,
  "results": [
    {
      "T": "AAPL",
      "o": 192.50,
      "h": 195.00,
      "l": 191.80,
      "c": 194.18,
      "v": 48200000,
      "vw": 193.42,
      "t": 1712188800000
    }
  ]
}
```

---

## Official Python Client (`massive`)

For projects that prefer the official SDK:

```bash
pip install -U massive
```

```python
from massive import RESTClient

client = RESTClient(api_key="YOUR_MASSIVE_API_KEY")

# Single ticker snapshot
snap = client.get_snapshot_ticker("stocks", "AAPL")
print(snap.day.close)

# Multiple tickers — iterate
tickers = ["AAPL", "TSLA", "NVDA"]
snapshots = client.get_snapshot_all_tickers("stocks", tickers=tickers)
for s in snapshots:
    print(s.ticker, s.last_trade.price)
```

The SDK handles authentication, pagination, and retry logic automatically.

---

## Choosing Between Endpoints

| Use Case                               | Recommended Endpoint                      |
|----------------------------------------|-------------------------------------------|
| Poll a watchlist of 10–250 tickers     | `GET /v2/snapshot/…/tickers?tickers=…`    |
| Cross-asset or >10 tickers cleanly     | `GET /v3/snapshot?ticker.any_of=…`        |
| On-demand lookup for a single ticker   | `GET /v2/snapshot/…/tickers/{ticker}`     |
| Most recent trade price                | `GET /v2/last/trade/{ticker}`             |
| Previous close for P&L baseline        | `GET /v2/aggs/ticker/{ticker}/prev`       |

For FinAlly's polling loop, **the multi-ticker snapshot** (`/v2/snapshot/locale/us/markets/stocks/tickers`) is the best fit: one request returns prices for all watchlist tickers and includes `todaysChange` / `todaysChangePerc` for session context.
