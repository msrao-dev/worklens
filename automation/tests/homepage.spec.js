
const { test, expect } = require('@playwright/test');

test('WorkLens homepage loads successfully', async ({ page }) => {
  const response = await page.goto('/');

  expect(response).not.toBeNull();
  expect(response.status()).toBeLessThan(400);

  await expect(page.locator('body')).toBeVisible();

  console.log('Page title:', await page.title());
});
