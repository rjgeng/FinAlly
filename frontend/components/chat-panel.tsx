"use client";

import { useRef, useState, useEffect, type FormEvent } from "react";
import { api, formatCurrency } from "@/lib/api";
import type {
  ChatActions,
  ChatExecutedTrade,
  ChatExecutedWatchlistChange,
  ChatResponse,
} from "@/types/api";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  actions?: ChatActions;
}

interface ChatPanelProps {
  onTradeExecuted: () => void;
  onWatchlistChanged: () => void;
}

function ActionCard({ actions }: { actions: ChatActions }) {
  const trades = actions.trades ?? [];
  const watchlistChanges = actions.watchlist_changes ?? [];
  if (trades.length === 0 && watchlistChanges.length === 0) return null;

  return (
    <div className="mt-1.5 space-y-1">
      {trades.map((t: ChatExecutedTrade, i: number) => (
        <div
          key={`trade-${i}`}
          data-testid="chat-trade-confirmation"
          className={`rounded-md px-2 py-1 text-xs font-medium ${
            t.status === "executed"
              ? "bg-up/10 text-up"
              : "bg-down/10 text-down"
          }`}
        >
          {t.status === "executed"
            ? `${t.side === "buy" ? "Bought" : "Sold"} ${t.executed_quantity} ${t.ticker} @ ${formatCurrency(t.executed_price ?? 0)}`
            : `Failed: ${t.error ?? "Unknown error"}`}
        </div>
      ))}
      {watchlistChanges.map((w: ChatExecutedWatchlistChange, i: number) => (
        <div
          key={`wl-${i}`}
          className={`rounded-md px-2 py-1 text-xs font-medium ${
            w.status === "applied"
              ? "bg-accent-blue/10 text-accent-blue"
              : "bg-down/10 text-down"
          }`}
        >
          {w.status === "applied"
            ? `${w.action === "add" ? "Added" : "Removed"} ${w.ticker} ${w.action === "add" ? "to" : "from"} watchlist`
            : `Failed: ${w.error ?? "Unknown error"}`}
        </div>
      ))}
    </div>
  );
}

export function ChatPanel({ onTradeExecuted, onWatchlistChanged }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, loading]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    const userMessage: ChatMessage = { role: "user", content: text };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    try {
      const res: ChatResponse = await api.sendChat(text);
      const assistantMessage: ChatMessage = {
        role: "assistant",
        content: res.message,
        actions: res.actions,
      };
      setMessages((prev) => [...prev, assistantMessage]);

      // Notify parent of side-effects
      const hasTrades = (res.actions?.trades?.length ?? 0) > 0;
      const hasWlChanges =
        (res.actions?.watchlist_changes?.length ?? 0) > 0;
      if (hasTrades) onTradeExecuted();
      if (hasWlChanges) onWatchlistChanged();
    } catch (err) {
      const errMessage: ChatMessage = {
        role: "assistant",
        content: `Error: ${err instanceof Error ? err.message : "Something went wrong"}`,
      };
      setMessages((prev) => [...prev, errMessage]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="flex h-full flex-col overflow-hidden rounded-lg border border-terminal-border bg-terminal-surface">
      <header className="border-b border-terminal-border px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-terminal-muted">
          AI Assistant
        </h2>
      </header>

      {/* Message list */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-3 py-3 space-y-3"
        data-testid="chat-messages"
      >
        {messages.length === 0 && !loading && (
          <p className="py-8 text-center text-xs text-terminal-muted">
            Ask FinAlly about your portfolio, request trades, or manage your
            watchlist.
          </p>
        )}
        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`flex ${
              msg.role === "user" ? "justify-end" : "justify-start"
            }`}
          >
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed ${
                msg.role === "user"
                  ? "bg-accent-blue/20 text-terminal-text"
                  : "bg-terminal-bg text-terminal-text"
              }`}
              data-testid={msg.role === "assistant" ? "chat-message-assistant" : undefined}
            >
              {msg.role === "assistant" && (
                <span className="mb-0.5 block text-[10px] font-semibold uppercase tracking-wider text-accent-yellow">
                  FinAlly
                </span>
              )}
              <p className="whitespace-pre-wrap">{msg.content}</p>
              {msg.actions && <ActionCard actions={msg.actions} />}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start" data-testid="chat-loading">
            <div className="rounded-lg bg-terminal-bg px-3 py-2 text-xs text-terminal-muted">
              <span className="inline-flex gap-1">
                <span className="animate-bounce" style={{ animationDelay: "0ms" }}>.</span>
                <span className="animate-bounce" style={{ animationDelay: "100ms" }}>.</span>
                <span className="animate-bounce" style={{ animationDelay: "200ms" }}>.</span>
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-2 border-t border-terminal-border bg-terminal-bg/40 p-3"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask FinAlly anything..."
          className="flex-1 rounded-md border border-terminal-border bg-terminal-bg px-3 py-2 text-sm text-terminal-text placeholder:text-terminal-muted focus:border-accent-purple focus:outline-none"
          disabled={loading}
          data-testid="chat-input"
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="rounded-md bg-accent-purple px-4 py-2 text-xs font-bold text-white transition-opacity disabled:opacity-50"
          data-testid="chat-send"
        >
          Send
        </button>
      </form>
    </section>
  );
}
