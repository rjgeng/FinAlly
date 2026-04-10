"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { WatchlistEntry } from "@/types/api";

export function useWatchlist() {
  const [tickers, setTickers] = useState<WatchlistEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getWatchlist();
      if (mounted.current) {
        setTickers(data.tickers ?? []);
        setError(null);
      }
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  const add = useCallback(
    async (ticker: string) => {
      const sym = ticker.trim().toUpperCase();
      if (!sym) return;
      try {
        await api.addToWatchlist(sym);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [refresh],
  );

  const remove = useCallback(
    async (ticker: string) => {
      try {
        await api.removeFromWatchlist(ticker);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [refresh],
  );

  useEffect(() => {
    mounted.current = true;
    refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  return { tickers, loading, error, add, remove, refresh };
}
