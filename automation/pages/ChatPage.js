const {expect} = require('@playwright/test');

class ChatPage {
    constructor(page) {
        this.page = page;
        this.questionInput = page.locator('textarea[name="q"]');
        this.sendButton = page.locator('button[type="submit"]');
        this.composer = page.locator('form.composer');
        // Add inside the constructor
        this.answer = page.locator('div.answer');
    }

    async expectChatbotVisible() {
        await expect(this.questionInput).toBeVisible();
        await expect(this.composer).toBeVisible();
    }

    async askQuestion(question) {
        await this.questionInput.fill(question);
        await expect(this.sendButton).toBeEnabled();
        await this.sendButton.click();
    }

    async expectAnswerReceived() {
        await expect(this.answer.last()).toBeVisible({
            timeout: 60000,
        });

        await expect(this.answer.last()).not.toBeEmpty();
    }

    async expectEmptyQuestionCannotBeSent() {
        await expect(this.questionInput).toHaveValue('');
        await expect(this.sendButton).toBeDisabled();
    }
}

module.exports = {ChatPage};