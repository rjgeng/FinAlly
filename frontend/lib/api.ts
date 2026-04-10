import type {
  ChatResponse,
  PortfolioHistoryResponse,
  PortfolioResponse,
  TradeRequest,
  TradeResponse,
  WatchlistMutationResponse,
  WatchlistResponse,
} from "@/types/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.error) detail = body.error;
      else if (body?.detail) detail = body.detail;
    } catch {
      // ignore parse errors
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  getWatchlist: () => request<WatchlistResponse>("/api/watchlist"),

  addToWatchlist: (ticker: string) =>
    request<WatchlistMutationResponse>("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ ticker: ticker.toUpperCase() }),
    }),

  removeFromWatchlist: (ticker: string) =>
    request<WatchlistMutationResponse>(
      `/api/watchlist/${encodeURIComponent(ticker.toUpperCase())}`,
      { method: "DELETE" },
    ),

  getPortfolio: () => request<PortfolioResponse>("/api/portfolio"),

  getPortfolioHistory: () =>
    request<PortfolioHistoryResponse>("/api/portfolio/history"),

  executeTrade: (body: TradeRequest) =>
    request<TradeResponse>("/api/portfolio/trade", {
      method: "POST",
      body: JSON.stringify({
        ticker: body.ticker.toUpperCase(),
        quantity: body.quantity,
        side: body.side,
      }),
    }),

  sendChat: (message: string) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
};

export function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatPercent(value: number, digits = 2): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function formatQuantity(value: number): string {
  return Number(value.toFixed(4)).toString();
}
