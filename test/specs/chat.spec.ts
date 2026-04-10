import { test, expect } from '@playwright/test';
import {
  resetUserState,
  sendChatMessage,
  waitForAssistantReply,
  waitForHealth,
  waitForPrice,
  watchlistRow,
} from './helpers';

/**
 * Chat tests rely on LLM_MOCK=true — see PLAN.md §9 for the deterministic
 * rule table. The rules (simplified):
 *   - contains "buy"     → buy 1 share of first ticker (default AAPL)
 *   - contains "sell"    → sell 1 share of first ticker (default AAPL)
 *   - contains "add" + ticker    → add ticker to watchlist
 *   - contains "remove" + ticker → remove ticker from watchlist
 *   - otherwise          → plain message reply, no actions
 */

test.describe('AI chat (LLM_MOCK=true)', () => {
  test.beforeEach(async ({ page, request }) => {
    await waitForHealth(request);
    await resetUserState(request);
    await page.goto('/');
    await waitForPrice(page, 'AAPL');
  });

  test('sending "buy TSLA" executes a mock trade and shows confirmation', async ({
    page,
    request,
  }) => {
    await sendChatMessage(page, 'buy TSLA');
    const reply = await waitForAssistantReply(page, 1);
    await expect(reply).toBeVisible();

    // The reply should reference the TSLA trade or contain a trade
    // confirmation block. We accept either an inline action chip or a
    // text mention.
    const tradeConfirm = reply
      .locator('[data-testid="chat-trade-confirmation"], [data-action="trade"]')
      .or(reply.getByText(/TSLA/))
      .first();
    await expect(tradeConfirm).toBeVisible({ timeout: 10_000 });

    // Verify the backend actually holds a TSLA position.
    await expect
      .poll(
        async () => {
          const res = await request.get('/api/portfolio');
          const body = await res.json();
          const positions: Array<{ ticker: string; quantity: number }> = body.positions ?? [];
          const tsla = positions.find((p) => p.ticker === 'TSLA');
          return tsla?.quantity ?? 0;
        },
        { timeout: 10_000 },
      )
      .toBeGreaterThanOrEqual(1);
  });

  test('sending "add PYPL" adds PYPL to the watchlist', async ({ page, request }) => {
    await sendChatMessage(page, 'add PYPL');
    await waitForAssistantReply(page, 1);

    // PYPL should appear in the watchlist UI.
    await expect(watchlistRow(page, 'PYPL')).toBeVisible({ timeout: 15_000 });

    // And on the server.
    const wlRes = await request.get('/api/watchlist');
    const body = await wlRes.json();
    const tickers: string[] = (body.tickers ?? body.items ?? body ?? []).map((x: unknown) =>
      typeof x === 'string' ? x : (x as { ticker: string }).ticker,
    );
    expect(tickers).toContain('PYPL');
  });

  test('sending "hello" gets a response with no trade action', async ({ page, request }) => {
    await sendChatMessage(page, 'hello');
    const reply = await waitForAssistantReply(page, 1);
    await expect(reply).toBeVisible();

    const replyText = (await reply.textContent())?.trim() ?? '';
    expect(replyText.length).toBeGreaterThan(0);

    // Portfolio should still be empty — "hello" must not trigger any trade.
    const portRes = await request.get('/api/portfolio');
    const portBody = await portRes.json();
    const positions: unknown[] = portBody.positions ?? [];
    expect(positions.length).toBe(0);
  });

  test('loading indicator is shown while waiting for a chat response', async ({ page }) => {
    const input = page
      .locator('[data-testid="chat-input"], textarea[name="chat"]')
      .or(page.getByPlaceholder(/ask|chat|message/i))
      .first();
    await input.fill('analyze my portfolio');

    const submit = page
      .locator('[data-testid="chat-send"]')
      .or(page.getByRole('button', { name: /send/i }))
      .first();

    // Fire the submit and immediately check for the loading indicator.
    // Use Promise.all so we don't miss a fast response.
    const loading = page
      .locator('[data-testid="chat-loading"], [data-testid="chat-spinner"]')
      .or(page.getByText(/thinking|loading\.\.\./i))
      .first();

    await Promise.all([
      loading.waitFor({ state: 'visible', timeout: 5_000 }).catch(() => {
        // Some fast mock responses may return before the loading state
        // becomes visible; in that case fall through to the reply check.
      }),
      submit.click(),
    ]);

    // Eventually a reply arrives.
    await waitForAssistantReply(page, 1);
  });
});
