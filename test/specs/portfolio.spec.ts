import { test, expect } from '@playwright/test';
import { resetUserState, submitTrade, waitForHealth, waitForPrice } from './helpers';

test.describe('Portfolio visualization', () => {
  test.beforeEach(async ({ page, request }) => {
    await waitForHealth(request);
    await resetUserState(request);
    await page.goto('/');
    await waitForPrice(page, 'AAPL');
  });

  test('positions table shows an empty state before any trades', async ({ page }) => {
    // Before any trade, the positions-table element should NOT exist, and the
    // "No open positions yet" empty-state text should appear on the page.
    await expect(page.locator('[data-testid="positions-table"]')).toHaveCount(0);
    await expect(page.getByText(/no open positions yet/i).first()).toBeVisible();
  });

  test('heatmap shows "No open positions yet" before any trades', async ({ page }) => {
    const heatmap = page
      .locator('[data-testid="portfolio-heatmap"], [data-testid="heatmap"]')
      .first();
    await expect(heatmap).toBeVisible({ timeout: 10_000 });
    // PLAN.md §13 locks the exact placeholder text.
    await expect(heatmap.getByText(/no open positions yet/i)).toBeVisible();
  });

  test('heatmap renders position rectangles after buying', async ({ page, request }) => {
    await submitTrade(page, 'AAPL', 2, 'buy');

    // Wait for the trade to settle on the backend.
    await expect
      .poll(
        async () => {
          const res = await request.get('/api/portfolio');
          const body = await res.json();
          return (body.positions ?? []).length;
        },
        { timeout: 10_000 },
      )
      .toBeGreaterThan(0);

    const heatmap = page
      .locator('[data-testid="portfolio-heatmap"], [data-testid="heatmap"]')
      .first();
    await expect(heatmap).toBeVisible();

    const cells = heatmap.locator(
      '[data-testid^="heatmap-cell"], [data-ticker], rect[data-ticker], .heatmap-cell',
    );
    await expect.poll(async () => cells.count(), { timeout: 10_000 }).toBeGreaterThanOrEqual(1);
  });

  test('P&L chart eventually shows data points', async ({ page, request }) => {
    // Snapshots are recorded periodically; even without a trade the chart may
    // start plotting. To make this deterministic, execute a trade and then
    // poll the history endpoint until it returns data.
    await submitTrade(page, 'AAPL', 1, 'buy');

    await expect
      .poll(
        async () => {
          const res = await request.get('/api/portfolio/history');
          if (!res.ok()) return 0;
          const body = await res.json();
          const snapshots: unknown[] = body.snapshots ?? body.history ?? body ?? [];
          return snapshots.length;
        },
        { timeout: 20_000, intervals: [500, 1000, 2000] },
      )
      .toBeGreaterThan(0);

    const chart = page
      .locator('[data-testid="pnl-chart"], [data-testid="portfolio-chart"]')
      .first();
    await expect(chart).toBeVisible({ timeout: 10_000 });
  });
});
