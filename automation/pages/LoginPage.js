const {expect} = require('@playwright/test');


class LoginPage {
    constructor(page) {
        this.page = page;

        // Locators identified from the WorkLens login page
        this.emailInput = page.locator('#wl-email');
        this.passwordInput = page.locator('#wl-password');
        this.signInButton = page.getByRole('button', {
            name: 'Sign in',
        });
        this.loginHeading = page.getByRole('heading', {
            name: 'Sign in',
        });
        this.loginError = this.page.getByRole('alert');
        this.signInAsText = this.page.getByText('Signed in as', {exact: false})
    }

    async goto() {
        await this.page.goto('/');
    }

    async expectLoginPageVisible() {
        await expect(this.loginHeading).toBeVisible();
        await expect(this.emailInput).toBeVisible();
        await expect(this.passwordInput).toBeVisible();
        await expect(this.signInButton).toBeVisible();
    }

    async enterEmail(email) {
        await this.emailInput.fill(email);
    }

    async enterPassword(password) {
        await this.passwordInput.fill(password);
    }

    async clickSignIn() {
        await this.signInButton.click();
    }

    async login(email, password) {
        await this.enterEmail(email);
        await this.enterPassword(password);
        await this.clickSignIn();
    }

    async expectLoginError(message) {
        await expect(this.loginError).toHaveText(message);
    }

    async expectSuccessfulLogin() {
        await expect(this.signInAsText).toBeVisible();
    }
}

module.exports = {LoginPage};
