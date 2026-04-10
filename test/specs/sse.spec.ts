import { test, expect } from '@playwright/test';
import { resetUserState, waitForHealth, waitForPrice } from './helpers';

test.describe('SSE streaming', () => {
  test.beforeEach(async ({ page, request }) => {
    await waitForHealth(request);
    await resetUserState(request);
    await page.goto('/');
  });

  test('connection status indicator reports connected (green)', async ({ page }) => {
    // Header holds a dot that can be green/yellow/red per PLAN §2.
    const indicator = page
      .locator(
        '[data-testid="connection-status"], [data-testid="sse-status"], [data-status="connected"]',
      )
      .first();
    await expect(indicator).toBeVisible({ timeout: 10_000 });

    // Accept either a data attribute or a green-coloured class / style.
    await expect
      .poll(
        async () => {
          const state = await indicator.getAttribute('data-status');
          if (state) return state;
          const className = (await indicator.getAttribute('class')) ?? '';
          if (/green|connected|online/i.test(className)) return 'connected';
          const style = (await indicator.getAttribute('style')) ?? '';
          if (/green|#0[df]|#22c/i.test(style)) return 'connected';
          return 'unknown';
        },
        { timeout: 10_000 },
      )
      .toMatch(/connected|green|online/i);
  });

  test('prices visibly change over time (SSE updates the DOM)', async ({ page }) => {
    // Wait for the first price to appear, then sample a second reading after
    // a short delay to verify the DOM is being updated by the stream.
    const firstRead = await waitForPrice(page, 'AAPL');
    expect(firstRead).toMatch(/\d+\.\d{2}/);

    // Poll for a change in either price text or a data attribute. The
    // simulator updates on ~500ms intervals (PLAN §6) so 15s is generous.
    await expect
      .poll(
        async () => {
          const text = await waitForPrice(page, 'AAPL', 5_000).catch(() => firstRead);
          return text;
        },
        { timeout: 15_000, intervals: [500, 1000] },
      )
      .not.toEqual(firstRead);
  });

  test('SSE endpoint responds with event stream content-type', async () => {
    // Use native fetch with an AbortController so we don't block forever on
    // the infinite SSE stream. Grab the response headers as soon as the
    // connection is established, then abort.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5_000);
    try {
      const res = await fetch('http://localhost:8000/api/stream/prices', {
        headers: { Accept: 'text/event-stream' },
        signal: controller.signal,
      });
      expect(res.status).toBe(200);
      const ctype = res.headers.get('content-type') ?? '';
      expect(ctype).toMatch(/text\/event-stream/);
    } finally {
      controller.abort();
      clearTimeout(timer);
    }
  });
});
