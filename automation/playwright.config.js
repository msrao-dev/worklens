
require('dotenv').config();
const { defineConfig } = require('@playwright/test');


module.exports = defineConfig({
  testDir: './tests',
  timeout: 30_000,

  expect: {
    timeout: 10_000,
  },

  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],

  use: {
    baseURL: process.env.WORKLENS_BASE_URL || 'http://localhost:4200',
    browserName: 'chromium',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
