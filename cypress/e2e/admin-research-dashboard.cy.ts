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

	it('exports selected participant sessions through the full JSON endpoint', () => {
		cy.intercept('GET', '**/api/v1/analytics/experiments/filters*', {
			groups: [],
			topics: [],
			states: []
		});
		cy.intercept('GET', '**/api/v1/analytics/experiments/participants*', {
			items: [
				{
					session_id: 'session-1',
					participant_id: 'P-TEST',
					name: 'Participant',
					group_name: 'Study',
					state: 'COMPLETED',
					consented: true,
					pre_survey_completed: true,
					essay_submitted: true,
					post_survey_completed: true,
					prompts: 1,
					total_tokens: 10,
					total_keystrokes: 20,
					pause_count: 0,
					total_pasted_chars: 0,
					tab_switch_count: 0,
					total_time_away_ms: 0
				}
			],
			total: 1,
			page: 1,
			limit: 25
		});
		cy.intercept('POST', '**/api/v1/analytics/experiments/export/sessions', (request) => {
			expect(request.body).to.deep.equal({ ids: ['session-1'], anonymized: true });
			request.reply({
				headers: {
					'content-type': 'application/json',
					'content-disposition': 'attachment; filename="experiment-full-sessions-test.json"'
				},
				body: JSON.stringify({ schema_version: '1.0', anonymized: true, sessions: [] })
			});
		}).as('fullSessionExport');

		cy.visit('/admin/analytics/participants');
		cy.contains('button', 'Export full sessions (JSON)').should('be.disabled');
		cy.get('table tbody input[type="checkbox"]').check();
		cy.contains('button', 'Export full sessions (JSON) (1)').click();
		cy.wait('@fullSessionExport');
		cy.contains('button', 'Export CSV').should('not.exist');
		cy.contains('button', 'Export JSON').should('not.exist');
	});
});
