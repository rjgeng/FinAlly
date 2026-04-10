"use client";

import { useCallback, useState } from "react";
import { usePriceStream } from "@/hooks/usePriceStream";
import { usePortfolio } from "@/hooks/usePortfolio";
import { useWatchlist } from "@/hooks/useWatchlist";

import { Header } from "./header";
import { WatchlistPanel } from "./watchlist-panel";
import { MainChart } from "./main-chart";
import { PortfolioHeatmap } from "./portfolio-heatmap";
import { PnLChart } from "./pnl-chart";
import { PositionsTable } from "./positions-table";
import { TradeBar } from "./trade-bar";
import { ChatPanel } from "./chat-panel";

/**
 * Root client component — wires all panels together into a
 * single-page trading terminal layout.
 *
 * Layout grid (desktop):
 * ┌────────────────────────────────────────┐
 * │ Header (full width)                    │
 * ├────────┬───────────────┬───────────────┤
 * │ Watch  │ Main Chart    │ AI Chat       │
 * │ list   │               │               │
 * │        ├───────┬───────┤               │
 * │        │Heatmap│ P&L   │               │
 * │        ├───────┴───────┤               │
 * │        │ Positions Tbl │ Trade Bar     │
 * └────────┴───────────────┴───────────────┘
 */
export default function Workstation() {
  const { prices, status } = usePriceStream();
  const { portfolio, refresh: refreshPortfolio } = usePortfolio();
  const {
    tickers: watchlistTickers,
    error: watchlistError,
    add: addTicker,
    remove: removeTicker,
    refresh: refreshWatchlist,
  } = useWatchlist();

  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  const handleSelectTicker = useCallback((ticker: string) => {
    setSelectedTicker(ticker);
  }, []);

  const handleTradeSuccess = useCallback(() => {
    refreshPortfolio();
  }, [refreshPortfolio]);

  const handleWatchlistChanged = useCallback(() => {
    refreshWatchlist();
  }, [refreshWatchlist]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-terminal-bg">
      {/* Header */}
      <Header
        totalValue={portfolio?.total_value ?? null}
        cashBalance={portfolio?.cash_balance ?? null}
        status={status}
      />

      {/* Body Grid */}
      <div className="grid flex-1 gap-2 overflow-hidden p-2 grid-cols-[260px_1fr_340px] grid-rows-[1fr_auto_auto]">
        {/* Column 1: Watchlist (spans all rows) */}
        <div className="row-span-3 overflow-hidden">
          <WatchlistPanel
            tickers={watchlistTickers}
            prices={prices}
            selectedTicker={selectedTicker}
            onSelectTicker={handleSelectTicker}
            onAddTicker={addTicker}
            onRemoveTicker={removeTicker}
            error={watchlistError}
          />
        </div>

        {/* Column 2, Row 1: Main chart */}
        <div className="min-h-[220px] overflow-hidden">
          <MainChart ticker={selectedTicker} prices={prices} />
        </div>

        {/* Column 3, Row 1-2: AI Chat (spans 2 rows) */}
        <div className="row-span-2 overflow-hidden">
          <ChatPanel
            onTradeExecuted={() => {
              handleTradeSuccess();
              handleWatchlistChanged();
            }}
            onWatchlistChanged={handleWatchlistChanged}
          />
        </div>

        {/* Column 2, Row 2: Heatmap + P&L side by side */}
        <div className="grid min-h-[180px] grid-cols-2 gap-2 overflow-hidden">
          <PortfolioHeatmap
            positions={portfolio?.positions ?? []}
            onSelectTicker={handleSelectTicker}
          />
          <PnLChart />
        </div>

        {/* Column 2, Row 3: Positions table */}
        <div className="overflow-hidden">
          <PositionsTable
            positions={portfolio?.positions ?? []}
            onSelectTicker={handleSelectTicker}
          />
        </div>

        {/* Column 3, Row 3: Trade bar */}
        <div className="overflow-hidden">
          <TradeBar
            prefillTicker={selectedTicker ?? ""}
            onTradeSuccess={handleTradeSuccess}
          />
        </div>
      </div>
    </div>
  );
}
