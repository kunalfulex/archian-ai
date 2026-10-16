import { test, expect } from '@playwright/test';

/**
 * F2B: planner clarification panel → same payloads as POST /architect/stream planner_resume.
 * Requires auth + backend with AGENT_PLANNER_V2=true.
 */
const EXPORT_PROMPT =
  'extract contact data from SF 1 org and deploy them to google sheet';

test.describe('Google Sheets planner F2B', () => {
  test.beforeEach(({ }, testInfo) => {
    test.skip(
      !process.env.E2E_FRONTEND_URL,
      'Set E2E_FRONTEND_URL (e.g. https://localhost:3000)'
    );
  });

  test('workbook picker appears for SF export to Google Sheets', async ({ page }) => {
    const chatPath = process.env.E2E_CHAT_PATH || '/chat';
    await page.goto(chatPath, { waitUntil: 'domcontentloaded', timeout: 30_000 });

    const input = page.locator('textarea, input[type="text"]').last();
    await input.fill(EXPORT_PROMPT);
    await page.keyboard.press('Enter');

    const clarPanel = page.locator('.planner-clarification-panel');
    await expect(clarPanel).toBeVisible({ timeout: 120_000 });
    await expect(clarPanel.getByText(/which google sheet/i)).toBeVisible();
    await expect(clarPanel.locator('.planner-clarification-option').first()).toBeVisible();
    await expect(
      clarPanel.getByRole('button', { name: /create new spreadsheet/i })
    ).toBeVisible();
  });

  test('clicking existing workbook does not show spreadsheet created success', async ({
    page,
  }) => {
    const chatPath = process.env.E2E_CHAT_PATH || '/chat';
    await page.goto(chatPath, { waitUntil: 'domcontentloaded', timeout: 30_000 });

    const input = page.locator('textarea, input[type="text"]').last();
    await input.fill(EXPORT_PROMPT);
    await page.keyboard.press('Enter');

    const clarPanel = page.locator('.planner-clarification-panel');
    await expect(clarPanel).toBeVisible({ timeout: 120_000 });

    const contactOption = clarPanel
      .locator('.planner-clarification-option')
      .filter({ hasText: /^contact list/i })
      .first();
    await expect(contactOption).toBeVisible({ timeout: 30_000 });
    await contactOption.click();

    const planPanel = page.locator('.planner-plan-panel');
    await expect(planPanel).toBeVisible({ timeout: 120_000 });
    const planText = await planPanel.innerText();
    expect(planText.toLowerCase()).not.toMatch(/create spreadsheet/);

    await planPanel.getByRole('button', { name: /run plan/i }).click();

    await expect(page.getByText(/spreadsheet created successfully/i)).not.toBeVisible({
      timeout: 180_000,
    });
  });

  test('create new spreadsheet panel option leads to plan with create step or name prompt', async ({
    page,
  }) => {
    const chatPath = process.env.E2E_CHAT_PATH || '/chat';
    await page.goto(chatPath, { waitUntil: 'domcontentloaded', timeout: 30_000 });

    const input = page.locator('textarea, input[type="text"]').last();
    await input.fill(EXPORT_PROMPT);
    await page.keyboard.press('Enter');

    const clarPanel = page.locator('.planner-clarification-panel');
    await expect(clarPanel).toBeVisible({ timeout: 120_000 });
    await clarPanel
      .getByRole('button', { name: /create new spreadsheet/i })
      .click();

    const namePrompt = page.locator('.planner-clarification-panel').last().getByText(
      /what should we name the new google sheet/i
    );
    const planPanel = page.locator('.planner-plan-panel').last();
    await expect(namePrompt.or(planPanel)).toBeVisible({ timeout: 120_000 });
    if (await planPanel.isVisible()) {
      const text = await planPanel.innerText();
      expect(text.toLowerCase()).toMatch(/create spreadsheet/);
    }
  });
});
