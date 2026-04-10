"use client";

import { useEffect, useRef } from "react";
import { formatCurrency } from "@/lib/api";
import type { PricesMap } from "@/hooks/usePriceStream";

interface MainChartProps {
  ticker: string | null;
  prices: PricesMap;
}

/**
 * Canvas line chart for the currently selected ticker using SSE history.
 */
export function MainChart({ ticker, prices }: MainChartProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const state = ticker ? prices[ticker] : undefined;
  const points = state?.history ?? [];

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const dpr = window.devicePixelRatio || 1;
    const w = container.clientWidth;
    const h = container.clientHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    if (points.length < 2) {
      ctx.fillStyle = "#30363d";
      ctx.font = "12px system-ui";
      ctx.textAlign = "center";
      ctx.fillText(
        ticker ? "Waiting for price data..." : "Select a ticker",
        w / 2,
        h / 2,
      );
      return;
    }

    const priceValues = points.map((p) => p.price);
    const min = Math.min(...priceValues);
    const max = Math.max(...priceValues);
    const range = max - min || 1;

    const padY = 16;
    const chartH = h - padY * 2;
    const stepX = w / (priceValues.length - 1);

    const toY = (v: number) => padY + chartH - ((v - min) / range) * chartH;

    // Gradient fill
    const gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, "rgba(32, 157, 215, 0.25)");
    gradient.addColorStop(1, "rgba(32, 157, 215, 0.00)");

    ctx.beginPath();
    priceValues.forEach((v, i) => {
      const x = i * stepX;
      const y = toY(v);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });

    // Stroke
    ctx.strokeStyle = "#209dd7";
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.stroke();

    // Fill
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // Y-axis labels (min / mid / max)
    ctx.fillStyle = "#8b949e";
    ctx.font = "10px system-ui";
    ctx.textAlign = "left";
    ctx.fillText(formatCurrency(max), 4, padY - 2);
    ctx.fillText(formatCurrency((max + min) / 2), 4, h / 2);
    ctx.fillText(formatCurrency(min), 4, h - padY + 10);
  }, [points, ticker]);

  return (
    <section className="flex h-full flex-col overflow-hidden rounded-lg border border-terminal-border bg-terminal-surface">
      <header className="flex items-center gap-3 border-b border-terminal-border px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-terminal-muted">
          Chart
        </h2>
        {ticker && (
          <>
            <span className="font-mono text-sm font-semibold text-terminal-text">
              {ticker}
            </span>
            {state && (
              <span className="tabular text-sm font-medium text-accent-blue">
                {formatCurrency(state.price)}
              </span>
            )}
          </>
        )}
      </header>
      <div ref={containerRef} className="relative flex-1">
        <canvas ref={canvasRef} className="absolute inset-0" />
      </div>
    </section>
  );
}
