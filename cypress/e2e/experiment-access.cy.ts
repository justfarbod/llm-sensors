// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../support/index.d.ts" />

describe('Experiment participant access', () => {
	beforeEach(() => {
		cy.loginExperimentUser();
		cy.visit('/');
	});

	it('shows chat and essay tools without prompt starters or non-experiment controls', () => {
		cy.get('#chat-input').should('exist');
		cy.get('button[aria-label="Essay"]').should('exist');
		cy.contains('Suggested').should('not.exist');
		cy.get('#temporary-chat-button').should('not.exist');
		cy.get('button[aria-label="Controls"]').should('not.exist');
	});

	it('restricts the profile menu to sign out', () => {
		cy.get('img[src*="/profile/image"]').last().click();
		cy.contains('Sign Out').should('exist');
		cy.contains('Settings').should('not.exist');
		cy.contains('Archived Chats').should('not.exist');
		cy.contains('Keyboard shortcuts').should('not.exist');
	});

	it('redirects blocked personal and workspace routes to the experiment interface', () => {
		cy.visit('/workspace');
		cy.location('pathname').should('eq', '/');
		cy.visit('/playground');
		cy.location('pathname').should('eq', '/');
	});
});
