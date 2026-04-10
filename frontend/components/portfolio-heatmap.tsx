"use client";

import { useMemo } from "react";
import { formatPercent } from "@/lib/api";
import type { Position } from "@/types/api";

interface PortfolioHeatmapProps {
  positions: Position[];
  onSelectTicker?: (ticker: string) => void;
}

/* ------------------------------------------------------------------
 * Simple squarified-treemap layout (Bruls, Huizing, van Wijk 2000).
 * Produces an array of { x, y, w, h } rectangles for each item.
 * ----------------------------------------------------------------*/

interface TreemapRect {
  ticker: string;
  pnlPct: number;
  value: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

function layoutTreemap(
  items: { ticker: string; pnlPct: number; value: number }[],
  width: number,
  height: number,
): TreemapRect[] {
  if (items.length === 0) return [];

  // Sort descending by absolute value
  const sorted = [...items].sort((a, b) => b.value - a.value);
  const totalValue = sorted.reduce((s, d) => s + d.value, 0);

  const rects: TreemapRect[] = [];

  function squarify(
    data: typeof sorted,
    x: number,
    y: number,
    w: number,
    h: number,
  ) {
    if (data.length === 0) return;
    if (data.length === 1) {
      rects.push({ ...data[0], x, y, w, h });
      return;
    }

    const areaFraction =
      data.reduce((s, d) => s + d.value, 0) / totalValue;
    const horizontal = w >= h;

    // Use a simple split at 50% of the local sum area.
    const target = areaFraction * totalValue * 0.5;
    let sum = 0;
    let splitIdx = 0;
    for (let i = 0; i < data.length; i++) {
      sum += data[i].value;
      if (sum >= target) {
        splitIdx = i;
        break;
      }
    }
    splitIdx = Math.max(0, Math.min(data.length - 2, splitIdx));

    const left = data.slice(0, splitIdx + 1);
    const right = data.slice(splitIdx + 1);
    const leftSum = left.reduce((s, d) => s + d.value, 0);
    const dataSum = data.reduce((s, d) => s + d.value, 0);
    const ratio = leftSum / dataSum;

    if (horizontal) {
      const splitW = w * ratio;
      squarify(left, x, y, splitW, h);
      squarify(right, x + splitW, y, w - splitW, h);
    } else {
      const splitH = h * ratio;
      squarify(left, x, y, w, splitH);
      squarify(right, x, y + splitH, w, h - splitH);
    }
  }

  squarify(sorted, 0, 0, width, height);
  return rects;
}

function pnlColor(pct: number): string {
  if (pct > 5) return "#22c55e";
  if (pct > 2) return "#16a34a";
  if (pct > 0) return "#15803d60";
  if (pct > -2) return "#b91c1c60";
  if (pct > -5) return "#dc2626";
  return "#ef4444";
}

export function PortfolioHeatmap({
  positions,
  onSelectTicker,
}: PortfolioHeatmapProps) {
  const WIDTH = 500;
  const HEIGHT = 220;

  const rects = useMemo(() => {
    const items = positions
      .filter((p) => p.quantity > 0)
      .map((p) => ({
        ticker: p.ticker,
        pnlPct: p.pnl_pct,
        value: Math.abs(p.quantity * p.current_price),
      }));
    return layoutTreemap(items, WIDTH, HEIGHT);
  }, [positions]);

  if (rects.length === 0) {
    return (
      <section data-testid="portfolio-heatmap" className="flex items-center justify-center rounded-lg border border-terminal-border bg-terminal-surface px-4 py-8">
        <span className="text-xs text-terminal-muted" data-testid="heatmap-empty">
          No open positions yet
        </span>
      </section>
    );
  }

  return (
    <section data-testid="portfolio-heatmap" className="overflow-hidden rounded-lg border border-terminal-border bg-terminal-surface">
      <header className="border-b border-terminal-border px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-terminal-muted">
          Portfolio Heatmap
        </h2>
      </header>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        data-testid="heatmap-svg"
      >
        {rects.map((r) => {
          const minDim = Math.min(r.w, r.h);
          const showLabel = minDim > 28;
          return (
            <g
              key={r.ticker}
              onClick={() => onSelectTicker?.(r.ticker)}
              className="cursor-pointer"
              data-testid={`heatmap-cell-${r.ticker}`}
              data-ticker={r.ticker}
            >
              <rect
                x={r.x + 1}
                y={r.y + 1}
                width={Math.max(0, r.w - 2)}
                height={Math.max(0, r.h - 2)}
                rx={4}
                fill={pnlColor(r.pnlPct)}
                opacity={0.85}
              />
              {showLabel && (
                <>
                  <text
                    x={r.x + r.w / 2}
                    y={r.y + r.h / 2 - 5}
                    textAnchor="middle"
                    fill="white"
                    fontSize={Math.min(14, r.w / 5)}
                    fontWeight="700"
                  >
                    {r.ticker}
                  </text>
                  <text
                    x={r.x + r.w / 2}
                    y={r.y + r.h / 2 + 12}
                    textAnchor="middle"
                    fill="white"
                    fontSize={Math.min(11, r.w / 6)}
                    opacity={0.9}
                  >
                    {formatPercent(r.pnlPct)}
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>
    </section>
  );
}
