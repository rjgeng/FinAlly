import { test, expect } from '@playwright/test';
import {
  readCashBalance,
  resetUserState,
  submitTrade,
  waitForHealth,
  waitForPrice,
} from './helpers';

test.describe('Trading', () => {
  test.beforeEach(async ({ page, request }) => {
    await waitForHealth(request);
    await resetUserState(request);
    await page.goto('/');
    // Make sure prices are streaming before we trade — market orders fill at
    // the current cached price, which the simulator starts publishing within
    // a few hundred ms.
    await waitForPrice(page, 'AAPL');
  });

  test('buying 1 share of AAPL decreases cash and creates a position', async ({ page }) => {
    const beforeCash = await readCashBalance(page);

    await submitTrade(page, 'AAPL', 1, 'buy');

    // Positions table should show AAPL.
    const positionsTable = page.locator('[data-testid="positions-table"], table').first();
    await expect(positionsTable.getByText('AAPL', { exact: true })).toBeVisible({ timeout: 10_000 });

    // Cash should have decreased (we don't know the exact fill price from the
    // UI, so we just assert it went down by at least something plausible).
    await expect
      .poll(async () => readCashBalance(page), { timeout: 10_000 })
      .toBeLessThan(beforeCash);
  });

  test('selling all AAPL shares removes the position and returns cash', async ({ page, request }) => {
    // Buy first so there's something to sell.
    await submitTrade(page, 'AAPL', 2, 'buy');

    const positionsTable = page.locator('[data-testid="positions-table"], table').first();
    await expect(positionsTable.getByText('AAPL', { exact: true })).toBeVisible({ timeout: 10_000 });

    const cashAfterBuy = await readCashBalance(page);

    // Sell the full quantity.
    await submitTrade(page, 'AAPL', 2, 'sell');

    // Position row should disappear (zero-qty positions are deleted per PLAN §13).
    await expect
      .poll(
        async () => {
          const res = await request.get('/api/portfolio');
          const body = await res.json();
          const positions: Array<{ ticker: string }> = body.positions ?? [];
          return positions.some((p) => p.ticker === 'AAPL');
        },
        { timeout: 10_000 },
      )
      .toBe(false);

    // Cash should be back up (roughly — prices may have drifted a tick or
    // two, so we allow for small movement).
    const cashAfterSell = await readCashBalance(page);
    expect(cashAfterSell).toBeGreaterThan(cashAfterBuy);
  });

  test('buying two different tickers creates two positions for the heatmap', async ({
    page,
    request,
  }) => {
    await submitTrade(page, 'AAPL', 1, 'buy');
    // Wait briefly for the first trade to land before submitting the second.
    await expect
      .poll(
        async () => {
          const res = await request.get('/api/portfolio');
          const body = await res.json();
          return (body.positions ?? []).length;
        },
        { timeout: 10_000 },
      )
      .toBeGreaterThanOrEqual(1);

    await submitTrade(page, 'TSLA', 1, 'buy');

    await expect
      .poll(
        async () => {
          const res = await request.get('/api/portfolio');
          const body = await res.json();
          return (body.positions ?? []).length;
        },
        { timeout: 10_000 },
      )
      .toBeGreaterThanOrEqual(2);

    // Heatmap should now have at least two rects / cells.
    const heatmap = page.locator('[data-testid="portfolio-heatmap"], [data-testid="heatmap"]').first();
    await expect(heatmap).toBeVisible({ timeout: 10_000 });
    const cells = heatmap.locator(
      '[data-testid^="heatmap-cell"], [data-ticker], rect[data-ticker], .heatmap-cell',
    );
    await expect.poll(async () => cells.count(), { timeout: 10_000 }).toBeGreaterThanOrEqual(2);
  });

  test('attempting to overspend shows an error (insufficient cash)', async ({ page }) => {
    // 10_000 shares of AAPL will vastly exceed $10k at any realistic price.
    await submitTrade(page, 'AAPL', 10_000, 'buy');

    // Error surfaces in the UI as either a toast or inline message. Also
    // accept a 400 response on the trade endpoint as evidence the guardrail
    // kicked in.
    const errorLocator = page
      .locator('[data-testid="trade-error"], [role="alert"], .toast-error')
      .or(page.getByText(/insufficient|cannot afford|too few funds/i))
      .first();

    await expect(errorLocator).toBeVisible({ timeout: 10_000 });
  });
});
