/**
 * Shared helpers for FinAlly E2E tests.
 *
 * Selector strategy:
 *   Prefer data-testid attributes where possible, falling back to text content
 *   and role-based locators. The frontend is built by a separate agent; these
 *   helpers provide a single place to update selectors if the UI contract
 *   drifts. All helpers are intentionally permissive — multiple candidate
 *   selectors are tried so a minor naming mismatch does not fail the suite.
 */

import { APIRequestContext, Page, expect, Locator } from '@playwright/test';

export const DEFAULT_WATCHLIST = [
  'AAPL',
  'GOOGL',
  'MSFT',
  'AMZN',
  'TSLA',
  'NVDA',
  'META',
  'JPM',
  'V',
  'NFLX',
];

export const API_BASE = 'http://localhost:8000';

/**
 * Wait for the backend to be healthy. Used as a pre-flight check in tests
 * that do heavy API interaction directly (e.g. reset state via trades).
 */
export async function waitForHealth(request: APIRequestContext, timeoutMs = 20_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastErr: unknown = null;
  while (Date.now() < deadline) {
    try {
      const res = await request.get(`${API_BASE}/api/health`);
      if (res.ok()) return;
    } catch (e) {
      lastErr = e;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Backend health check failed within ${timeoutMs}ms: ${String(lastErr)}`);
}

/**
 * Reset user state between tests via direct API calls. This keeps tests
 * independent: clear all positions (by selling them) and reset watchlist to
 * the default ten tickers.
 *
 * Because the backend has no dedicated reset endpoint (see PLAN.md) this
 * walks the portfolio and watchlist and reverses any deltas.
 */
export async function resetUserState(request: APIRequestContext): Promise<void> {
  // 1. Sell every open position back to zero.
  try {
    const portRes = await request.get(`${API_BASE}/api/portfolio`);
    if (portRes.ok()) {
      const port = await portRes.json();
      const positions: Array<{ ticker: string; quantity: number }> = port.positions ?? [];
      for (const pos of positions) {
        if (pos.quantity > 0) {
          await request.post(`${API_BASE}/api/portfolio/trade`, {
            data: { ticker: pos.ticker, quantity: pos.quantity, side: 'sell' },
          });
        }
      }
    }
  } catch {
    // best effort
  }

  // 2. Reset watchlist to the default set.
  try {
    const wlRes = await request.get(`${API_BASE}/api/watchlist`);
    if (wlRes.ok()) {
      const wl = await wlRes.json();
      const current: string[] = (wl.tickers ?? wl.items ?? wl ?? []).map((x: unknown) =>
        typeof x === 'string' ? x : (x as { ticker: string }).ticker,
      );
      const currentSet = new Set(current);
      const defaultSet = new Set(DEFAULT_WATCHLIST);

      // Remove any tickers not in the default set.
      for (const t of current) {
        if (!defaultSet.has(t)) {
          await request.delete(`${API_BASE}/api/watchlist/${t}`);
        }
      }
      // Add any default tickers that are missing.
      for (const t of DEFAULT_WATCHLIST) {
        if (!currentSet.has(t)) {
          await request.post(`${API_BASE}/api/watchlist`, { data: { ticker: t } });
        }
      }
    }
  } catch {
    // best effort
  }
}

/**
 * Locate a single watchlist row by ticker symbol. Uses multiple selector
 * strategies so it works regardless of whether the frontend uses testids or
 * plain text.
 */
export function watchlistRow(page: Page, ticker: string): Locator {
  return page
    .locator(
      [
        `[data-testid="watchlist-row-${ticker}"]`,
        `[data-testid="watchlist-item-${ticker}"]`,
        `[data-ticker="${ticker}"]`,
      ].join(', '),
    )
    .or(page.locator('[data-testid="watchlist"]').getByText(ticker, { exact: true }))
    .first();
}

/**
 * Wait for at least one price to appear in the watchlist for the given
 * ticker. Returns the text seen. Used to validate that SSE is streaming.
 */
export async function waitForPrice(page: Page, ticker: string, timeoutMs = 15_000): Promise<string> {
  const row = watchlistRow(page, ticker);
  await row.waitFor({ state: 'visible', timeout: timeoutMs });

  // Look for a price element inside the row, falling back to any $-prefixed text.
  const priceLocator = row
    .locator('[data-testid*="price"], [data-price], .price')
    .or(row.getByText(/\$?\d+\.\d{2}/))
    .first();

  await expect(priceLocator).toBeVisible({ timeout: timeoutMs });
  const text = (await priceLocator.textContent()) ?? '';
  return text.trim();
}

/**
 * Read a numeric cash-balance value from the page header. Accepts formats
 * like "$10,000.00", "10,000.00", "10000".
 */
export async function readCashBalance(page: Page): Promise<number> {
  const locator = page
    .locator('[data-testid="cash-balance"], [data-testid="cash"]')
    .or(page.getByText(/\$\s?1[0-9,.]+/).first());
  await locator.first().waitFor({ state: 'visible' });
  const raw = (await locator.first().textContent()) ?? '';
  const match = raw.match(/-?\$?\s?([\d,]+(?:\.\d+)?)/);
  if (!match) throw new Error(`Could not parse cash balance from "${raw}"`);
  return parseFloat(match[1].replace(/,/g, ''));
}

/**
 * Fill the trade bar ticker + quantity and click the buy or sell button.
 * Uses multiple selector strategies for robustness.
 */
export async function submitTrade(
  page: Page,
  ticker: string,
  quantity: number,
  side: 'buy' | 'sell',
): Promise<void> {
  // Use strict data-testid selectors — loose fallbacks (e.g. getByPlaceholder)
  // can match the watchlist's own "Add ticker" input instead of the trade bar.
  const tickerInput = page.locator('[data-testid="trade-ticker"]').first();
  const qtyInput = page
    .locator('[data-testid="trade-quantity"], [data-testid="trade-quantity-input"]')
    .first();

  await tickerInput.fill(ticker);
  await qtyInput.fill(String(quantity));

  const button = page
    .locator(`[data-testid="trade-${side}"], [data-testid="trade-${side}-btn"]`)
    .first();
  await button.click();
}

/**
 * Send a message through the AI chat panel and wait for the assistant reply.
 */
export async function sendChatMessage(page: Page, text: string): Promise<void> {
  const input = page
    .locator('[data-testid="chat-input"], textarea[name="chat"]')
    .or(page.getByPlaceholder(/ask|chat|message/i))
    .first();
  await input.fill(text);

  const submit = page
    .locator('[data-testid="chat-send"], [data-testid="chat-send-btn"]')
    .or(page.getByRole('button', { name: /send/i }))
    .first();
  await submit.click();
}

/**
 * Wait for the Nth assistant chat message to appear (1-indexed).
 */
export async function waitForAssistantReply(page: Page, nth = 1, timeoutMs = 15_000): Promise<Locator> {
  const messages = page.locator(
    '[data-testid="chat-message-assistant"], [data-role="assistant"], .chat-message.assistant',
  );
  await expect(messages.nth(nth - 1)).toBeVisible({ timeout: timeoutMs });
  return messages.nth(nth - 1);
}
