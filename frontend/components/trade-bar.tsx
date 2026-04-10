"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface TradeBarProps {
  prefillTicker: string;
  onTradeSuccess: () => void;
}

export function TradeBar({ prefillTicker, onTradeSuccess }: TradeBarProps) {
  const [ticker, setTicker] = useState(prefillTicker);
  const [quantity, setQuantity] = useState<string>("1");
  const [status, setStatus] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);

  // Sync prefill when parent changes the selected ticker
  // Using a controlled pattern: only overwrite if user hasn't typed
  if (prefillTicker && prefillTicker !== ticker && !loading) {
    setTicker(prefillTicker);
  }

  const doTrade = async (side: "buy" | "sell") => {
    const sym = ticker.trim().toUpperCase();
    const qty = parseFloat(quantity);
    if (!sym || Number.isNaN(qty) || qty <= 0) {
      setStatus({ type: "error", message: "Enter a valid ticker and quantity > 0" });
      return;
    }
    setLoading(true);
    setStatus(null);
    try {
      const res = await api.executeTrade({ ticker: sym, quantity: qty, side });
      if (res.success) {
        setStatus({
          type: "success",
          message: `${side === "buy" ? "Bought" : "Sold"} ${qty} ${res.ticker} @ $${res.price.toFixed(2)}`,
        });
        onTradeSuccess();
      } else {
        setStatus({ type: "error", message: res.error ?? "Trade failed" });
      }
    } catch (err) {
      setStatus({
        type: "error",
        message: err instanceof Error ? err.message : "Trade failed",
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="flex flex-col gap-2 rounded-lg border border-terminal-border bg-terminal-surface p-3">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-terminal-muted">
        Trade
      </h2>
      <div className="flex items-center gap-2">
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="TICKER"
          className="w-24 rounded-md border border-terminal-border bg-terminal-bg px-2 py-1.5 font-mono text-sm text-terminal-text placeholder:text-terminal-muted focus:border-accent-blue focus:outline-none"
          maxLength={10}
          name="ticker"
          data-testid="trade-ticker"
        />
        <input
          type="number"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          min="0"
          step="any"
          placeholder="Qty"
          className="w-20 rounded-md border border-terminal-border bg-terminal-bg px-2 py-1.5 font-mono text-sm text-terminal-text placeholder:text-terminal-muted focus:border-accent-blue focus:outline-none"
          name="quantity"
          data-testid="trade-quantity"
        />
        <button
          type="button"
          disabled={loading}
          onClick={() => doTrade("buy")}
          className="rounded-md bg-accent-blue px-4 py-1.5 text-xs font-bold text-white transition-opacity disabled:opacity-50"
          data-testid="trade-buy"
        >
          Buy
        </button>
        <button
          type="button"
          disabled={loading}
          onClick={() => doTrade("sell")}
          className="rounded-md bg-down px-4 py-1.5 text-xs font-bold text-white transition-opacity disabled:opacity-50"
          data-testid="trade-sell"
        >
          Sell
        </button>
      </div>
      {status && (
        <p
          className={`text-xs ${status.type === "success" ? "text-up" : "text-down"}`}
          data-testid={status.type === "error" ? "trade-error" : "trade-status"}
          role={status.type === "error" ? "alert" : undefined}
        >
          {status.message}
        </p>
      )}
    </section>
  );
}
