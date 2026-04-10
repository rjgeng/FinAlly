"use client";

import { useEffect, useRef } from "react";

interface SparklineProps {
  points: number[];
  width?: number;
  height?: number;
  color?: string;
}

/**
 * Tiny canvas sparkline — no axis, no labels, just a smooth line.
 * Auto-scales to the min/max of the provided points.
 */
export function Sparkline({
  points,
  width = 96,
  height = 28,
  color = "#209dd7",
}: SparklineProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    if (points.length < 2) {
      // Draw a flat neutral baseline so the cell isn't empty.
      ctx.strokeStyle = "#30363d";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, height / 2);
      ctx.lineTo(width, height / 2);
      ctx.stroke();
      return;
    }

    const min = Math.min(...points);
    const max = Math.max(...points);
    const range = max - min || 1;
    const stepX = width / (points.length - 1);

    // Tint direction: up/down relative to first point.
    const isUp = points[points.length - 1] >= points[0];
    const strokeColor = color ?? (isUp ? "#22c55e" : "#ef4444");

    // Fill gradient under the line for a slight glow.
    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, `${strokeColor}55`);
    gradient.addColorStop(1, `${strokeColor}00`);

    ctx.beginPath();
    points.forEach((p, i) => {
      const x = i * stepX;
      const y = height - ((p - min) / range) * (height - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });

    // Stroke the line
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    ctx.stroke();

    // Fill under the line
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();
  }, [points, width, height, color]);

  return <canvas ref={canvasRef} />;
}
