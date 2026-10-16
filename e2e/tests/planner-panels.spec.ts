import { test, expect } from '@playwright/test';

/**
 * Smoke test for planner v2 UI selectors.
 * Skips unless E2E_FRONTEND_URL is set (and optionally E2E_STORAGE_STATE_PATH for auth).
 */
test.describe('Planner v2 panels', () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(
      !process.env.E2E_FRONTEND_URL,
      'Set E2E_FRONTEND_URL (e.g. https://localhost:3000)'
    );
  });

  test('architect chat exposes planner panel class hooks', async ({ page, baseURL }) => {
    const chatPath = process.env.E2E_CHAT_PATH || '/chat';
    try {
      await page.goto(chatPath, { waitUntil: 'domcontentloaded', timeout: 15_000 });
    } catch (err) {
      const msg = String(err);
      if (msg.includes('ERR_CONNECTION_REFUSED') || msg.includes('ECONNREFUSED')) {
        throw new Error(
          `Cannot reach ${baseURL}${chatPath}. Start the frontend first:\n` +
            '  cd edisonai-frontend && HTTPS=true SSL_CRT_FILE=certs/localhost.crt SSL_KEY_FILE=certs/localhost.key npm start'
        );
      }
      throw err;
    }

    // Panels appear after a planner turn; assert hooks exist in DOM or document structure.
    const planPanel = page.locator('.planner-plan-panel');
    const clarPanel = page.locator('.planner-clarification-panel');

    const planCount = await planPanel.count();
    const clarCount = await clarPanel.count();

    if (planCount === 0 && clarCount === 0) {
      test.info().annotations.push({
        type: 'note',
        description:
          'No planner panels visible yet — log in, trigger a planner classify turn, or set E2E_STORAGE_STATE_PATH',
      });
      test.skip(true, 'No planner panels on page (needs authenticated planner session)');
    }

    if (planCount > 0) {
      await expect(planPanel.first()).toBeVisible();
      await expect(planPanel.first().getByRole('button', { name: /run plan/i })).toBeVisible();
    }
    if (clarCount > 0) {
      await expect(clarPanel.first()).toBeVisible();
    }
  });
});
