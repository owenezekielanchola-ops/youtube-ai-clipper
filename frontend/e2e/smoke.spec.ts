import { test, expect } from '@playwright/test';

/**
 * Smoke tests: the app shell renders and core editor chrome is present,
 * independent of any backend AI calls. These guard against build/runtime
 * regressions that unit tests cannot catch (bundling, mount, routing).
 */
test.describe('ClipForge app shell', () => {
  test('loads with the correct document title', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/ClipForge/i);
  });

  test('mounts the React root without a blank screen', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('/');
    const root = page.locator('#root');
    await expect(root).not.toBeEmpty();
    expect(errors, `uncaught page errors: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('shows core editor controls (Export, Captions)', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('Export', { exact: false }).first()).toBeVisible();
    await expect(page.getByText('Captions', { exact: false }).first()).toBeVisible();
  });
});

/**
 * Full ingest -> edit -> export journey. Requires a running backend with
 * GROQ_API_KEY + GEMINI_API_KEY and FFmpeg. Enable with RUN_FULL_E2E=1.
 */
test.describe('Full clip journey', () => {
  test.skip(!process.env.RUN_FULL_E2E, 'needs backend + API keys + FFmpeg (set RUN_FULL_E2E=1)');

  test('ingest a short video and produce a clip', async ({ page }) => {
    await page.goto('/');
    // Scaffold — fill once real keys/fixtures are wired:
    // await page.getByPlaceholder(/youtube url/i).fill(process.env.E2E_YT_URL!);
    // await page.getByRole('button', { name: /clip|ingest/i }).click();
    // await expect(page.getByText(/highlight/i).first()).toBeVisible({ timeout: 120_000 });
  });
});
