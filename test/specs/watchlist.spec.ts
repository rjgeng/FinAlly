import { test, expect } from '@playwright/test';
import {
  DEFAULT_WATCHLIST,
  resetUserState,
  waitForHealth,
  waitForPrice,
  watchlistRow,
  readCashBalance,
} from './helpers';

test.describe('Watchlist', () => {
  test.beforeEach(async ({ page, request }) => {
    await waitForHealth(request);
    await resetUserState(request);
    await page.goto('/');
  });

  test('fresh start shows all 10 default watchlist tickers', async ({ page }) => {
    for (const ticker of DEFAULT_WATCHLIST) {
      await expect(watchlistRow(page, ticker)).toBeVisible({ timeout: 15_000 });
    }
  });

  test('initial cash balance is $10,000', async ({ page }) => {
    const balance = await readCashBalance(page);
    expect(balance).toBeCloseTo(10_000, 0);
  });

  test('prices stream in via SSE (at least one ticker has a price)', async ({ page }) => {
    const priceText = await waitForPrice(page, 'AAPL');
    expect(priceText).toMatch(/\d+\.\d{2}/);
  });

  test('can add a ticker to the watchlist (PYPL)', async ({ page }) => {
    const input = page
      .locator('[data-testid="watchlist-add-input"], input[name="add-ticker"]')
      .or(page.getByPlaceholder(/add ticker|add symbol/i))
      .first();
    await input.fill('PYPL');

    const addButton = page
      .locator('[data-testid="watchlist-add-button"]')
      .or(page.getByRole('button', { name: /^add$/i }))
      .first();
    await addButton.click();

    await expect(watchlistRow(page, 'PYPL')).toBeVisible({ timeout: 15_000 });
  });

  test('can remove a ticker from the watchlist', async ({ page, request }) => {
    const targetTicker = 'NFLX';
    const row = watchlistRow(page, targetTicker);
    await expect(row).toBeVisible({ timeout: 15_000 });

    // The remove button is hidden until the row is hovered
    // (Tailwind `group-hover:block`). Dispatch a click directly on the
    // button element so we don't have to fight the hover / visibility
    // actionability checks.
    const removeButton = row.locator('[data-testid="watchlist-remove"]').first();
    await removeButton.evaluate((el: HTMLElement) => el.click());

    await expect(row).toBeHidden({ timeout: 10_000 });

    // Verify server-side removal too.
    const wlRes = await request.get('/api/watchlist');
    expect(wlRes.ok()).toBeTruthy();
    const wl = await wlRes.json();
    const tickers: string[] = (wl.tickers ?? wl.items ?? wl ?? []).map((x: unknown) =>
      typeof x === 'string' ? x : (x as { ticker: string }).ticker,
    );
    expect(tickers).not.toContain(targetTicker);
  });

  test('clicking a ticker prefills the trade bar ticker field', async ({ page }) => {
    const row = watchlistRow(page, 'MSFT');
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.click();

    const tickerInput = page.locator('[data-testid="trade-ticker"]').first();
    await expect(tickerInput).toHaveValue('MSFT', { timeout: 5_000 });
  });
});
