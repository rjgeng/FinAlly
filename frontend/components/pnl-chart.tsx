"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { api, formatCurrency } from "@/lib/api";
import type { PortfolioSnapshot } from "@/types/api";

const POLL_INTERVAL_MS = 30_000;

/**
 * Line chart of portfolio value over time (from /api/portfolio/history).
 * Uses canvas for performance.
 */
export function PnLChart() {
  const [snapshots, setSnapshots] = useState<PortfolioSnapshot[]>([]);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getPortfolioHistory();
      setSnapshots(data.snapshots ?? []);
    } catch {
      // silently ignore fetch errors
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

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

    if (snapshots.length < 2) {
      ctx.fillStyle = "#30363d";
      ctx.font = "12px system-ui";
      ctx.textAlign = "center";
      ctx.fillText("Waiting for portfolio history...", w / 2, h / 2);
      return;
    }

    const values = snapshots.map((s) => s.total_value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;

    const padY = 16;
    const chartH = h - padY * 2;
    const stepX = w / (values.length - 1);
    const toY = (v: number) => padY + chartH - ((v - min) / range) * chartH;

    // Gradient fill
    const gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, "rgba(236, 173, 10, 0.30)");
    gradient.addColorStop(1, "rgba(236, 173, 10, 0.00)");

    ctx.beginPath();
    values.forEach((v, i) => {
      const x = i * stepX;
      const y = toY(v);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });

    ctx.strokeStyle = "#ecad0a";
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.stroke();

    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // Y labels
    ctx.fillStyle = "#8b949e";
    ctx.font = "10px system-ui";
    ctx.textAlign = "left";
    ctx.fillText(formatCurrency(max), 4, padY - 2);
    ctx.fillText(formatCurrency(min), 4, h - padY + 10);
  }, [snapshots]);

  return (
    <section data-testid="pnl-chart" className="flex h-full flex-col overflow-hidden rounded-lg border border-terminal-border bg-terminal-surface">
      <header className="border-b border-terminal-border px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-terminal-muted">
          Portfolio Value
        </h2>
      </header>
      <div ref={containerRef} className="relative flex-1 min-h-[120px]">
        <canvas ref={canvasRef} className="absolute inset-0" />
      </div>
    </section>
  );
}
