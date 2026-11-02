
const { test } = require('@playwright/test');
const { LoginPage } = require('../pages/LoginPage');

test('WorkLens login page renders correctly', async ({ page }) => {
  const loginPage = new LoginPage(page);

  await loginPage.goto();
  await loginPage.expectLoginPageVisible();
});

test('Login rejects invalid credentials', async ({ page }) => {
  const loginPage = new LoginPage(page);

  await loginPage.goto();
  await loginPage.login(
    'invalid-user@example.com',
    'InvalidPassword123!'
  );

  await loginPage.expectLoginError('Invalid email or password.');
});

test('Valid user can sign in successfully', async ({ page }) => {
  const loginPage = new LoginPage(page);

  await loginPage.goto();

  await loginPage.login(
    process.env.WORKLENS_TEST_EMAIL,
    process.env.WORKLENS_TEST_PASSWORD
  );

  await loginPage.expectSuccessfulLogin();
});
