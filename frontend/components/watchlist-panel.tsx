"use client";

import { useEffect, useRef, useState } from "react";
import { formatCurrency, formatPercent } from "@/lib/api";
import type { PricesMap } from "@/hooks/usePriceStream";
import type { WatchlistEntry } from "@/types/api";
import { Sparkline } from "./sparkline";

interface WatchlistPanelProps {
  tickers: WatchlistEntry[];
  prices: PricesMap;
  selectedTicker: string | null;
  onSelectTicker: (ticker: string) => void;
  onAddTicker: (ticker: string) => Promise<void> | void;
  onRemoveTicker: (ticker: string) => Promise<void> | void;
  error?: string | null;
}

/**
 * A single row in the watchlist. Tracks previous-price locally so we can
 * trigger the CSS flash animation when the streamed price changes.
 */
function WatchlistRow({
  entry,
  live,
  selected,
  onSelect,
  onRemove,
}: {
  entry: WatchlistEntry;
  live: PricesMap[string] | undefined;
  selected: boolean;
  onSelect: () => void;
  onRemove: () => void;
}) {
  const [flashClass, setFlashClass] = useState<"" | "flash-up" | "flash-down">(
    "",
  );
  const prevPrice = useRef<number | null>(null);

  // Prefer live SSE data; fall back to server-rendered snapshot.
  const currentPrice = live?.price ?? entry.price ?? null;
  const sessionChange = live?.sessionChangePct ?? 0;
  const history = live?.history.map((p) => p.price) ?? [];

  useEffect(() => {
    if (currentPrice == null) return;
    if (prevPrice.current != null && currentPrice !== prevPrice.current) {
      setFlashClass(currentPrice > prevPrice.current ? "flash-up" : "flash-down");
      const id = window.setTimeout(() => setFlashClass(""), 650);
      prevPrice.current = currentPrice;
      return () => window.clearTimeout(id);
    }
    prevPrice.current = currentPrice;
  }, [currentPrice]);

  const changeColor =
    sessionChange > 0
      ? "text-up"
      : sessionChange < 0
        ? "text-down"
        : "text-terminal-muted";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`group grid cursor-pointer grid-cols-[1fr_auto_auto] items-center gap-3 rounded-md border border-transparent px-3 py-2 transition-colors ${
        selected
          ? "border-accent-blue/40 bg-accent-blue/10"
          : "hover:bg-terminal-surface/60"
      } ${flashClass}`}
      data-testid={`watchlist-item-${entry.ticker}`}
    >
      <div className="flex min-w-0 flex-col">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-terminal-text">
            {entry.ticker}
          </span>
          <button
            type="button"
            aria-label={`remove ${entry.ticker}`}
            data-testid="watchlist-remove"
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            className="hidden text-xs text-terminal-muted transition-colors hover:text-down group-hover:block"
          >
            ×
          </button>
        </div>
        <span className={`tabular text-xs ${changeColor}`}>
          {formatPercent(sessionChange)}
        </span>
      </div>
      <Sparkline points={history} />
      <div
        className="tabular text-right text-sm font-medium text-terminal-text"
        data-testid={`price-${entry.ticker}`}
        data-price={currentPrice ?? undefined}
      >
        {currentPrice != null ? formatCurrency(currentPrice) : "—"}
      </div>
    </div>
  );
}

export function WatchlistPanel({
  tickers,
  prices,
  selectedTicker,
  onSelectTicker,
  onAddTicker,
  onRemoveTicker,
  error,
}: WatchlistPanelProps) {
  const [newTicker, setNewTicker] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const sym = newTicker.trim();
    if (!sym) return;
    setSubmitting(true);
    try {
      await onAddTicker(sym);
      setNewTicker("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section data-testid="watchlist" className="flex h-full flex-col overflow-hidden rounded-lg border border-terminal-border bg-terminal-surface">
      <header className="flex items-center justify-between border-b border-terminal-border px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-terminal-muted">
          Watchlist
        </h2>
        <span className="tabular text-xs text-terminal-muted">
          {tickers.length}
        </span>
      </header>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {tickers.length === 0 && (
          <p className="px-3 py-6 text-center text-xs text-terminal-muted">
            No tickers watched yet.
          </p>
        )}
        {tickers.map((entry) => (
          <WatchlistRow
            key={entry.ticker}
            entry={entry}
            live={prices[entry.ticker]}
            selected={selectedTicker === entry.ticker}
            onSelect={() => onSelectTicker(entry.ticker)}
            onRemove={() => void onRemoveTicker(entry.ticker)}
          />
        ))}
      </div>

      <form
        onSubmit={handleAdd}
        className="flex items-center gap-2 border-t border-terminal-border bg-terminal-bg/40 p-3"
      >
        <input
          value={newTicker}
          onChange={(e) => setNewTicker(e.target.value.toUpperCase())}
          placeholder="Add ticker (e.g. NVDA)"
          className="flex-1 rounded-md border border-terminal-border bg-terminal-bg px-2 py-1.5 text-sm text-terminal-text placeholder:text-terminal-muted focus:border-accent-blue focus:outline-none"
          maxLength={10}
          data-testid="watchlist-add-input"
        />
        <button
          type="submit"
          disabled={submitting || !newTicker.trim()}
          className="rounded-md bg-accent-blue px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="watchlist-add-button"
        >
          Add
        </button>
      </form>

      {error && (
        <p className="border-t border-terminal-border bg-down/10 px-3 py-2 text-xs text-down">
          {error}
        </p>
      )}
    </section>
  );
}
