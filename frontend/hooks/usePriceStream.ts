"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { Direction, PriceEvent } from "@/types/api";

export type ConnectionStatus =
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "idle";

export interface TickerState {
  price: number;
  previousPrice: number | null;
  direction: Direction;
  timestamp: string;
  sessionOpen: number;
  sessionChangePct: number;
  history: { t: number; price: number }[];
}

export type PricesMap = Record<string, TickerState>;

const MAX_HISTORY_POINTS = 500;
const MAX_RECONNECT_DELAY_MS = 30_000;
const INITIAL_RECONNECT_DELAY_MS = 1_000;

/**
 * Subscribes to the backend SSE price stream and maintains a per-ticker
 * in-memory cache of price + accumulated history since page load.
 *
 * - `session open` = first observed price in the current session
 * - `sessionChangePct` = ((current - sessionOpen) / sessionOpen) * 100
 * - reconnect uses exponential backoff capped at 30s
 */
export function usePriceStream() {
  const [prices, setPrices] = useState<PricesMap>({});
  const [status, setStatus] = useState<ConnectionStatus>("idle");

  const esRef = useRef<EventSource | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttempts = useRef(0);

  const applyPriceEvent = useCallback((event: PriceEvent) => {
    setPrices((prev) => {
      const existing = prev[event.ticker];
      const sessionOpen = existing?.sessionOpen ?? event.price;
      const sessionChangePct =
        sessionOpen > 0 ? ((event.price - sessionOpen) / sessionOpen) * 100 : 0;

      const historyPoint = {
        t: Date.parse(event.timestamp) || Date.now(),
        price: event.price,
      };

      const nextHistory = existing
        ? [...existing.history, historyPoint]
        : [historyPoint];

      if (nextHistory.length > MAX_HISTORY_POINTS) {
        nextHistory.splice(0, nextHistory.length - MAX_HISTORY_POINTS);
      }

      return {
        ...prev,
        [event.ticker]: {
          price: event.price,
          previousPrice: event.previous_price,
          direction: event.direction,
          timestamp: event.timestamp,
          sessionOpen,
          sessionChangePct,
          history: nextHistory,
        },
      };
    });
  }, []);

  const connect = useCallback(() => {
    if (typeof window === "undefined") return;

    // Clean up any previous connection
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    setStatus((cur) => (cur === "connected" ? cur : "reconnecting"));

    const es = new EventSource("/api/stream/prices");
    esRef.current = es;

    es.onopen = () => {
      reconnectAttempts.current = 0;
      setStatus("connected");
    };

    const handlePriceMessage = (raw: string) => {
      try {
        const data = JSON.parse(raw) as PriceEvent;
        if (data && typeof data.ticker === "string") {
          applyPriceEvent(data);
        }
      } catch {
        // ignore malformed payloads
      }
    };

    // Default channel — some servers emit untyped messages
    es.onmessage = (evt) => handlePriceMessage(evt.data);

    // Named channels per the PLAN: "price" and "heartbeat"
    es.addEventListener("price", (evt) => {
      handlePriceMessage((evt as MessageEvent).data);
    });
    es.addEventListener("heartbeat", () => {
      setStatus("connected");
    });

    es.onerror = () => {
      setStatus("reconnecting");
      es.close();
      esRef.current = null;

      const attempt = reconnectAttempts.current + 1;
      reconnectAttempts.current = attempt;
      const delay = Math.min(
        INITIAL_RECONNECT_DELAY_MS * 2 ** (attempt - 1),
        MAX_RECONNECT_DELAY_MS,
      );

      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      reconnectTimer.current = setTimeout(() => connect(), delay);
    };
  }, [applyPriceEvent]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      setStatus("disconnected");
    };
  }, [connect]);

  const getHistory = useCallback(
    (ticker: string): { t: number; price: number }[] =>
      prices[ticker]?.history ?? [],
    [prices],
  );

  return { prices, status, getHistory };
}
