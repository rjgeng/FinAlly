"use client";

import { formatCurrency, formatPercent, formatQuantity } from "@/lib/api";
import type { Position } from "@/types/api";

interface PositionsTableProps {
  positions: Position[];
  onSelectTicker?: (ticker: string) => void;
}

export function PositionsTable({ positions, onSelectTicker }: PositionsTableProps) {
  if (positions.length === 0) {
    return (
      <section className="flex items-center justify-center rounded-lg border border-terminal-border bg-terminal-surface px-4 py-8">
        <span className="text-xs text-terminal-muted">
          No open positions yet
        </span>
      </section>
    );
  }

  return (
    <section className="overflow-hidden rounded-lg border border-terminal-border bg-terminal-surface">
      <header className="border-b border-terminal-border px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-terminal-muted">
          Positions
        </h2>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full text-xs" data-testid="positions-table">
          <thead>
            <tr className="border-b border-terminal-border text-left text-terminal-muted">
              <th className="px-4 py-2 font-medium">Ticker</th>
              <th className="px-4 py-2 text-right font-medium">Qty</th>
              <th className="px-4 py-2 text-right font-medium">Avg Cost</th>
              <th className="px-4 py-2 text-right font-medium">Current</th>
              <th className="px-4 py-2 text-right font-medium">P&L</th>
              <th className="px-4 py-2 text-right font-medium">P&L %</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((pos) => {
              const pnlColor =
                pos.unrealized_pnl > 0
                  ? "text-up"
                  : pos.unrealized_pnl < 0
                    ? "text-down"
                    : "text-terminal-muted";
              return (
                <tr
                  key={pos.ticker}
                  className="cursor-pointer border-b border-terminal-border/50 transition-colors hover:bg-terminal-bg/30"
                  onClick={() => onSelectTicker?.(pos.ticker)}
                >
                  <td className="px-4 py-2 font-semibold text-terminal-text">
                    {pos.ticker}
                  </td>
                  <td className="tabular px-4 py-2 text-right text-terminal-text">
                    {formatQuantity(pos.quantity)}
                  </td>
                  <td className="tabular px-4 py-2 text-right text-terminal-text">
                    {formatCurrency(pos.avg_cost)}
                  </td>
                  <td className="tabular px-4 py-2 text-right text-terminal-text">
                    {formatCurrency(pos.current_price)}
                  </td>
                  <td className={`tabular px-4 py-2 text-right ${pnlColor}`}>
                    {formatCurrency(pos.unrealized_pnl)}
                  </td>
                  <td className={`tabular px-4 py-2 text-right ${pnlColor}`}>
                    {formatPercent(pos.pnl_pct)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
