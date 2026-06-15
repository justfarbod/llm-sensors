// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../support/index.d.ts" />

describe('Admin-only research dashboard', () => {
	beforeEach(() => {
		cy.loginAdmin();
	});

	it('redirects normal app routes to the research overview without a chat sidebar', () => {
		cy.visit('/');
		cy.url().should('include', '/admin/analytics/overview');
		cy.contains('Researcher Dashboard').should('exist');
		cy.get('#chat-search').should('not.exist');

		cy.visit('/playground');
		cy.url().should('include', '/admin/analytics/overview');
	});

	it('keeps technical admin pages under Advanced', () => {
		cy.visit('/admin/analytics/overview');
		cy.contains('button', 'Advanced').click();
		cy.contains('a', 'Functions').should('exist');
		cy.contains('a', 'Evaluations').should('exist');
		cy.contains('a', 'Settings').should('exist');
		cy.contains('a', 'System Analytics').should('exist');
	});

	it('renders research overview filters and handles an empty participant table', () => {
		cy.visit('/admin/analytics/overview');
		cy.get('select[aria-label="Group filter"]').should('exist');
		cy.get('select[aria-label="Topic filter"]').should('exist');
		cy.contains('Total sessions').should('exist');

		cy.contains('a', 'Participants').click();
		cy.contains(/assigned participant records|No participants match these filters/).should('exist');
	});
});
