# FinAlly — AI Trading Workstation

A visually rich, AI-powered trading workstation with live-streaming market data, a simulated portfolio, and an LLM chat assistant that can analyze positions and execute trades on your behalf.

## Quick Start

```bash
cp .env.example .env
# Add your OPENROUTER_API_KEY to .env
./scripts/start_unix.sh
```

Open [http://localhost:8000](http://localhost:8000). No login required.

## Features

- **Live price streaming** — tickers flash green/red on uptick/downtick via SSE
- **Sparkline charts** — per-ticker mini-charts accumulated from the live stream
- **Simulated trading** — $10,000 virtual cash, instant market-order fills
- **Portfolio heatmap** — treemap sized by weight, colored by P&L
- **AI chat assistant** — natural language portfolio analysis, trade execution, and watchlist management

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | Powers the LLM chat assistant |
| `MASSIVE_API_KEY` | No | Real market data; simulator used if unset |
| `LLM_MOCK` | No | Set `true` for deterministic mock responses (testing) |

## Architecture

Single Docker container on port 8000:

- **Frontend**: Next.js (TypeScript), static export served by FastAPI
- **Backend**: FastAPI + Python (`uv`), SQLite database
- **Real-time**: Server-Sent Events (`/api/stream/prices`)
- **LLM**: LiteLLM → OpenRouter (Cerebras inference)

## Scripts

```bash
./scripts/start_unix.sh [--build]   # Build and run
./scripts/stop_unix.sh              # Stop and remove container
```

Windows equivalents: `scripts/start_windows.ps1` / `scripts/stop_windows.ps1`

## Testing

```bash
cd test && docker compose -f docker-compose.test.yml up
```

Runs Playwright E2E tests against a containerized instance with `LLM_MOCK=true`.
