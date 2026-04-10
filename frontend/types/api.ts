// Shared API type definitions for FinAlly frontend.
// Contracts come from planning/PLAN.md and the team brief.

export type Direction = "up" | "down" | "flat";

export interface PriceEvent {
  ticker: string;
  price: number;
  previous_price: number | null;
  timestamp: string;
  direction: Direction;
}

export interface WatchlistEntry {
  ticker: string;
  price: number | null;
  previous_price: number | null;
  direction: Direction;
  timestamp: string | null;
}

export interface WatchlistResponse {
  tickers: WatchlistEntry[];
}

export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  unrealized_pnl: number;
  pnl_pct: number;
}

export interface PortfolioResponse {
  cash_balance: number;
  positions: Position[];
  total_value: number;
  total_pnl: number;
}

export interface PortfolioSnapshot {
  total_value: number;
  recorded_at: string;
}

export interface PortfolioHistoryResponse {
  snapshots: PortfolioSnapshot[];
}

export interface TradeRequest {
  ticker: string;
  quantity: number;
  side: "buy" | "sell";
}

export interface TradeResponse {
  success: boolean;
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  error: string | null;
}

export interface WatchlistAddRequest {
  ticker: string;
}

export interface WatchlistMutationResponse {
  success: boolean;
  ticker: string;
  error?: string | null;
}

export interface ChatExecutedTrade {
  ticker: string;
  side: "buy" | "sell";
  requested_quantity: number;
  executed_quantity: number;
  executed_price: number | null;
  status: "executed" | "failed";
  error: string | null;
}

export interface ChatExecutedWatchlistChange {
  ticker: string;
  action: "add" | "remove";
  status: "applied" | "failed";
  error: string | null;
}

export interface ChatActions {
  trades: ChatExecutedTrade[];
  watchlist_changes: ChatExecutedWatchlistChange[];
}

export interface ChatResponse {
  message: string;
  actions: ChatActions;
}
