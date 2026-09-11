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

	it('retains users, groups, task authoring and workflow editing', () => {
		cy.viewport(1440, 1000);
		cy.visit('/admin/users');
		cy.contains('a', 'Groups').click();
		cy.location('pathname').should('include', '/admin/users/groups');
		cy.visit('/admin/essays');
		cy.contains('h2', 'New Essay Topic').should('be.visible');
		cy.contains('label', 'Topic title').find('input').type('Profile validation topic');
		cy.get('textarea[aria-label="Essay question"]').type('Explain **the evidence**.');
		cy.intercept('POST', '**/api/v1/essays/topics/create', (request) => {
			expect(request.body).to.deep.equal({
				title: 'Profile validation topic',
				question: 'Explain **the evidence**.'
			});
			request.reply({ id: 'profile-topic', ...request.body });
		}).as('createTopic');
		cy.contains('button', 'Save Topic').click();
		cy.wait('@createTopic');
		cy.contains('button', 'Question Tasks').click();
		cy.contains('h2', 'New Question Task').should('be.visible');
		cy.contains('button', 'Workflows').click();
		cy.contains('Experiment Workflow Library').should('be.visible');
		cy.contains('button', 'New workflow').click();
		cy.contains('label', 'Workflow name').find('input').type('Profile workflow draft');
		cy.contains('Ordered tasks').should('be.visible');
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
