"use client";

import { formatCurrency } from "@/lib/api";
import type { ConnectionStatus } from "@/hooks/usePriceStream";

interface HeaderProps {
  totalValue: number | null;
  cashBalance: number | null;
  status: ConnectionStatus;
}

const statusColor: Record<ConnectionStatus, string> = {
  connected: "bg-up",
  reconnecting: "bg-accent-yellow pulse-dot",
  disconnected: "bg-down",
  idle: "bg-terminal-muted",
};

const statusLabel: Record<ConnectionStatus, string> = {
  connected: "Connected",
  reconnecting: "Reconnecting",
  disconnected: "Disconnected",
  idle: "Connecting...",
};

export function Header({ totalValue, cashBalance, status }: HeaderProps) {
  return (
    <header className="flex items-center justify-between border-b border-terminal-border bg-terminal-surface px-6 py-3">
      <div className="flex items-center gap-6">
        <h1 className="text-lg font-bold tracking-tight text-accent-yellow">
          FinAlly
        </h1>
        <div className="flex items-center gap-4 text-sm">
          <span className="text-terminal-muted">Portfolio</span>
          <span
            className="tabular font-semibold text-terminal-text"
            data-testid="portfolio-total-value"
          >
            {totalValue != null ? formatCurrency(totalValue) : "—"}
          </span>
          <span className="text-terminal-muted">Cash</span>
          <span
            className="tabular font-semibold text-terminal-text"
            data-testid="cash-balance"
          >
            {cashBalance != null ? formatCurrency(cashBalance) : "—"}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <span
          className={`inline-block h-2.5 w-2.5 rounded-full ${statusColor[status]}`}
          title={statusLabel[status]}
          data-testid="connection-status"
          data-status={status === "idle" ? "disconnected" : status}
        />
        <span className="text-xs text-terminal-muted">
          {statusLabel[status]}
        </span>
      </div>
    </header>
  );
}
