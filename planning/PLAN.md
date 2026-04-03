# FinAlly — AI Trading Workstation

## Project Specification v1.1

## 1. Vision

FinAlly (Finance Ally) is a visually rich AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It should feel like a modern, compact trading terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by coding agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

* A watchlist of 10 default tickers with live-updating prices in a grid
* $10,000 in virtual cash
* A dark, data-rich trading terminal aesthetic
* An AI chat panel ready to assist

### What the User Can Do

* **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
* **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load
* **Click a ticker** to see a larger detailed chart in the main chart area and prefill the trade bar ticker field
* **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
* **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
* **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
* **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
* **Manage the watchlist** — add/remove tickers manually or via the AI chat

### Visual Design

* **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
* **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
* **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
* **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
* **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme

* Accent Yellow: `#ecad0a`
* Blue Primary: `#209dd7`
* Purple Secondary: `#753991`

## 3. Architecture Overview

### Single Container, Single Port

```text
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving        │
│                      (Next.js export)           │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim       │
└─────────────────────────────────────────────────┘
```

* **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
* **Backend**: FastAPI (Python), managed as a `uv` project
* **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
* **Real-time data**: Server-Sent Events (SSE)
* **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
* **Market data**: environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision                | Rationale                                                                                    |
| ----------------------- | -------------------------------------------------------------------------------------------- |
| SSE over WebSockets     | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export   | Single origin, no CORS issues, one port, one container, simple deployment                    |
| SQLite over Postgres    | No auth = no multi-user = no need for a database server; self-contained, zero config         |
| Single Docker container | Students run one command; no docker-compose required for core usage                          |
| uv for Python           | Fast, modern Python project management; reproducible lockfile                                |
| Market orders only      | Eliminates order book, limit order logic, partial fills, and most portfolio complexity       |

## 4. Directory Structure

```text
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── schema/              # Schema definitions, seed data, migration/init logic
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_unix.sh         # Launch Docker container (macOS/Linux)
│   ├── stop_unix.sh          # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── docker-compose.yml        # Optional convenience wrapper
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

* **`frontend/`** is a self-contained Next.js project. It talks to the backend via `/api/*` and `/api/stream/*`.
* **`backend/`** is a self-contained `uv` project. It owns database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration.
* **`backend/schema/`** contains schema SQL definitions and seed logic.
* **`db/`** at the top level is the runtime volume mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts.
* **`planning/`** contains project-wide documentation, including this plan.
* **`test/`** contains Playwright E2E tests and supporting infrastructure.
* **`scripts/`** contains start/stop scripts that wrap Docker commands.

## 5. Environment Variables

```bash
# Required for real LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive API key for real market data
# If not set, the built-in market simulator is used
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false
```

### Behavior

* If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
* If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
* If `LLM_MOCK=true` → backend returns deterministic mock LLM responses
* The backend reads `.env` from the project root

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code is agnostic to the source.

### Simulator (Default)

* Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
* Updates at ~500ms intervals
* Correlated moves across related tickers
* Occasional random "events" — sudden 2–5% moves on a ticker
* Starts from realistic seed prices
* Runs as an in-process background task

### Massive API (Optional)

* REST API polling
* Polls for the union of all watched tickers
* Re-reads the current watchlist on each poll cycle so newly added tickers are picked up automatically
* Free tier guidance: poll every 15 seconds
* Paid tiers: poll every 2–15 seconds depending on tier
* Parses responses into the same normalized internal price format as the simulator

### Shared Price Cache

* A single background task writes to an in-memory price cache
* The cache holds the latest price, previous price, and timestamp for each ticker
* The cache may also hold backend metadata useful for streaming, but frontend chart history is accumulated client-side from SSE after page load
* SSE streams read from this cache and push updates to connected clients

### SSE Streaming

* Endpoint: `GET /api/stream/prices`
* Long-lived SSE connection; client uses native `EventSource`
* Server pushes price updates when the cache changes for a ticker
* Server also sends a lightweight heartbeat event every 10–15 seconds to support connection health and reconnection UX
* Each price event contains: ticker, price, previous_price, timestamp, and direction (`up`, `down`, `flat`)
* The frontend accumulates SSE data per ticker for both sparklines and the main chart
* In Massive mode, updates are only emitted when fresh poll results change cached prices; there is no fixed 500ms rebroadcast of identical data

## 7. Database

### SQLite with Lazy Initialization

The backend checks for the SQLite database on startup or first request. If the file does not exist or tables are missing, it creates the schema and seeds default data.

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now but preserves a future multi-user path.

**users_profile** — user state

* `id` TEXT PRIMARY KEY (default: `"default"`)
* `cash_balance` REAL (default: `10000.0`)
* `created_at` TEXT (ISO timestamp)

**watchlist** — tickers the user is watching

* `user_id` TEXT
* `ticker` TEXT
* `added_at` TEXT (ISO timestamp)
* PRIMARY KEY `(user_id, ticker)`

**positions** — current holdings

* `user_id` TEXT
* `ticker` TEXT
* `quantity` REAL
* `avg_cost` REAL
* `updated_at` TEXT (ISO timestamp)
* PRIMARY KEY `(user_id, ticker)`

**trades** — trade history (append-only log)

* `id` TEXT PRIMARY KEY
* `user_id` TEXT
* `ticker` TEXT
* `side` TEXT (`"buy"` or `"sell"`)
* `quantity` REAL
* `price` REAL
* `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — portfolio value over time

* `id` TEXT PRIMARY KEY
* `user_id` TEXT
* `total_value` REAL
* `recorded_at` TEXT (ISO timestamp)

**chat_messages** — conversation history with LLM

* `id` TEXT PRIMARY KEY
* `user_id` TEXT
* `role` TEXT (`"user"` or `"assistant"`)
* `content` TEXT
* `actions` TEXT (JSON post-execution summary; null for user messages)
* `created_at` TEXT (ISO timestamp)

### Data Rules

* Fractional shares are supported
* When a sell reduces a position quantity to `0`, the position row is deleted
* Watchlist and positions are independent; selling out of a position does **not** remove the ticker from the watchlist

### Default Seed Data

* One user profile: `id="default"`, `cash_balance=10000.0`
* Ten watchlist entries: `AAPL`, `GOOGL`, `MSFT`, `AMZN`, `TSLA`, `NVDA`, `META`, `JPM`, `V`, `NFLX`

## 8. API Endpoints

### Market Data

| Method | Path                 | Description                      |
| ------ | -------------------- | -------------------------------- |
| GET    | `/api/stream/prices` | SSE stream of live price updates |

### Portfolio

| Method | Path                     | Description                                                  |
| ------ | ------------------------ | ------------------------------------------------------------ |
| GET    | `/api/portfolio`         | Current positions, cash balance, total value, unrealized P&L |
| POST   | `/api/portfolio/trade`   | Execute a trade: `{ticker, quantity, side}`                  |
| GET    | `/api/portfolio/history` | Portfolio value snapshots over time                          |

### Watchlist

| Method | Path                      | Description                                                                     |
| ------ | ------------------------- | ------------------------------------------------------------------------------- |
| GET    | `/api/watchlist`          | Current watchlist tickers with latest cached prices                             |
| POST   | `/api/watchlist`          | Add a ticker: `{ticker}`                                                        |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker                                                                 |

### Chat

| Method | Path        | Description                                                                 |
| ------ | ----------- | --------------------------------------------------------------------------- |
| POST   | `/api/chat` | Send a message, receive complete JSON response (message + executed actions) |

### System

| Method | Path          | Description  |
| ------ | ------------- | ------------ |
| GET    | `/api/health` | Health check |

## 9. LLM Integration

When writing code to make calls to LLMs, use the cerebras-inference skill to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured outputs should be used.

There is an `OPENROUTER_API_KEY` in the project root `.env` file.

### Chat Flow

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total value)
2. Loads the recent conversation history from `chat_messages` (last 20 messages maximum)
3. Constructs a prompt with a system message, portfolio context, history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter requesting structured output
5. Parses the structured JSON response
6. Auto-executes any trades or watchlist changes in the response
7. Stores the assistant message and executed actions in `chat_messages`
8. Returns the response payload to the frontend

### Structured Output Schema

The LLM must respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"}
  ]
}
```

Rules:

* `message` is required
* `trades` is optional
* `watchlist_changes` is optional
* `watchlist_changes.action` valid values: `"add" | "remove"`

### Post-Execution Actions Payload

The backend stores and returns a post-execution summary so the frontend can render deterministic confirmations:

```json
{
  "trades": [
    {
      "ticker": "AAPL",
      "side": "buy",
      "requested_quantity": 10,
      "executed_quantity": 10,
      "executed_price": 192.14,
      "status": "executed",
      "error": null
    }
  ],
  "watchlist_changes": [
    {
      "ticker": "PYPL",
      "action": "add",
      "status": "applied",
      "error": null
    }
  ]
}
```

### Auto-Execution

Trades specified by the LLM execute automatically. This is deliberate because:

* It is a simulated environment with fake money
* It creates a smooth demo experience
* It demonstrates agentic AI capabilities clearly

If a trade fails validation, the failure is included in the post-execution summary so the assistant can explain what happened.

### System Prompt Guidance

The LLM should be prompted as **FinAlly, an AI trading assistant** and instructed to:

* Analyze portfolio composition, concentration, and P&L
* Suggest trades with concise reasoning
* Execute trades when the user asks or agrees
* Manage the watchlist proactively
* Be concise and data-driven
* Always return valid structured JSON

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic rule-based responses instead of calling OpenRouter. Rules are evaluated in order against the lowercased user message:

| Message contains | Response |
| ---------------- | -------- |
| `"buy"` | Buy 1 share of the first ticker mentioned, defaulting to `AAPL` |
| `"sell"` | Sell 1 share of the first ticker mentioned, defaulting to `AAPL` |
| `"add"` + a ticker | Add that ticker to the watchlist |
| `"remove"` + a ticker | Remove that ticker from the watchlist |
| anything else | No-action assistant reply only |

Example mock response for a "buy TSLA" message:

```json
{
  "message": "Mock mode: buy 1 TSLA.",
  "trades": [{"ticker": "TSLA", "side": "buy", "quantity": 1}],
  "watchlist_changes": []
}
```

## 10. Frontend Design

### Layout

The frontend is a single-page application with a compact terminal-inspired layout.

Required UI elements:

* **Watchlist panel** — watched tickers with ticker symbol, current price, session change %, price flash state, and sparkline mini-chart
* **Main chart area** — larger chart for the selected ticker, using accumulated SSE data since page load
* **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight and colored by P&L
* **P&L chart** — line chart showing total portfolio value over time using `portfolio_snapshots`
* **Positions table** — ticker, quantity, avg cost, current price, unrealized P&L, % change
* **Trade bar** — ticker input, quantity input, buy button, sell button; clicking a ticker in the watchlist should prefill the ticker field
* **AI chat panel** — message input, conversation history, loading indicator, inline action confirmations
* **Header** — portfolio total value, connection status indicator, cash balance

### Empty States

* On first launch, the heatmap should show a placeholder state such as **"No open positions yet"**
* The main chart can show the currently selected watchlist ticker and begin filling as SSE data arrives
* The positions table can show an empty state until the first trade

### Technical Notes

* Use `EventSource` for SSE connection to `/api/stream/prices`
* Canvas-based charting is preferred for performance
* Price flash effect: apply a transient CSS class on price change, then remove it
* All API calls go to the same origin (`/api/*`)
* Tailwind CSS for styling with a custom dark theme
* Session change % is computed on the frontend as change since first observed SSE price for that ticker in the current page session

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```text
Stage 1: Node 20 slim
  - Copy frontend/
  - npm install && npm run build

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/
  - uv sync
  - Copy frontend build output into a static/ directory
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

FastAPI serves the static frontend files and API routes on port 8000.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

### Start/Stop Scripts

**`scripts/start_unix.sh`**:

* Builds the Docker image if needed or when `--build` is passed
* Runs the container with the volume mount, port mapping, and `.env` file
* Prints the URL to access the app
* May optionally open the browser

**`scripts/stop_unix.sh`**:

* Stops and removes the running container
* Does not remove the persistent volume

**Windows PowerShell equivalents** should provide the same behavior.

All scripts should be idempotent.

## 12. Testing Strategy

### Unit Tests

**Backend (pytest)**

* Market data: simulator generates valid prices, parsing works, both implementations conform to the same interface
* Portfolio: trade execution logic, P&L calculations, insufficient cash, oversell protection
* LLM: structured output parsing, malformed response handling, chat flow validation
* API routes: status codes, response shape, error behavior

**Frontend**

* Component rendering with mock data
* Price flash animation triggers correctly on price changes
* Watchlist CRUD operations
* Portfolio display rendering
* Chat message rendering and loading state
* Session change % computation

### E2E Tests

Infrastructure: separate `docker-compose.test.yml` in `test/` that spins up the app plus a Playwright container.

Environment: tests run with `LLM_MOCK=true` by default.

Key scenarios:

* Fresh start: default watchlist appears, $10k balance shown, prices stream
* Add and remove a ticker from the watchlist
* Buy shares: cash decreases, position appears, portfolio updates
* Sell shares: cash increases, position updates or disappears when quantity reaches zero
* Portfolio visualization: heatmap renders after positions exist, P&L chart has data points
* AI chat (mocked): send a message, receive a response, trade execution appears inline
* SSE resilience: disconnect and verify reconnection state handling

## 13. Locked v1 Decisions

These decisions are intentionally fixed for v1 and should not be re-opened unless implementation reveals a concrete problem:

1. Use **session change %**, not true day change %
2. Main chart is **client-accumulated from SSE since page load**
3. SSE is **event-driven on cache change** plus heartbeat, not constant rebroadcast
4. Zero-quantity positions are **deleted**
5. Watchlist actions are exactly **`add`** and **`remove`**
6. `chat_messages.actions` stores **post-execution summaries**, not raw LLM intent
7. LLM history window is **last 20 messages**
8. Watchlist and positions are **independent**
9. Use the name **Massive API** consistently
10. Massive poller reads the watchlist **dynamically each cycle**
11. Clicking a watchlist ticker **prefills the trade bar ticker**
12. Empty heatmap shows **"No open positions yet"**
13. `LLM_MOCK=true` uses the rule-based deterministic mock defined in this document

## 14. Implementation Notes for Agents

* Prefer simple, explicit contracts over inferred behavior
* Avoid building extra endpoints unless required by this plan
* Keep frontend and backend boundaries clean
* Optimize for reproducible local startup and stable E2E tests
* Treat this file as the implementation contract for v1
