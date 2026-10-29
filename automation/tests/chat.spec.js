const {test} = require('@playwright/test');
const {LoginPage} = require('../pages/LoginPage');
const {ChatPage} = require('../pages/ChatPage');

test.describe('WorkLens Knowledge Assistant', () => {
    test.beforeEach(async ({page}) => {
        const loginPage = new LoginPage(page);

        await loginPage.goto();
        await loginPage.login(
            process.env.WORKLENS_TEST_EMAIL,
            process.env.WORKLENS_TEST_PASSWORD
        );
        await loginPage.expectSuccessfulLogin();
    });

    test('Chatbot is visible after login', async ({page}) => {
        const chatPage = new ChatPage(page);
        await chatPage.expectChatbotVisible();
    });

    test('Empty question cannot be submitted', async ({page}) => {
        const chatPage = new ChatPage(page);
        await chatPage.expectEmptyQuestionCannotBeSent();
    });

    test('Assistant returns an answer to a valid question', async ({page}) => {
        const chatPage = new ChatPage(page);

        await chatPage.expectChatbotVisible();
        await chatPage.askQuestion('What is the leave policy?');
        await chatPage.expectAnswerReceived();
    });
});