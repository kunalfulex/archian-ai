import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

const baseURL = process.env.E2E_FRONTEND_URL || 'https://localhost:3000';
const defaultAuth = path.join(__dirname, 'auth.json');
const storageState = process.env.E2E_FRONTEND_URL
  ? process.env.E2E_STORAGE_STATE_PATH || defaultAuth
  : undefined;

export default defineConfig({
  globalSetup: process.env.E2E_FRONTEND_URL ? require.resolve('./global-setup.ts') : undefined,
  testDir: './tests',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL,
    trace: 'on-first-retry',
    storageState,
    ignoreHTTPSErrors: true,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
