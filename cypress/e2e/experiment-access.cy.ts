// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../support/index.d.ts" />

describe('Experiment participant access', () => {
	beforeEach(() => {
		cy.viewport(1440, 1000);
		cy.loginExperimentUser();
		cy.visit('/');
		cy.get('#chat-input').should('be.visible');
	});

	it('shows chat and essay tools without prompt starters or non-experiment controls', () => {
		cy.get('#chat-input').should('exist');
		cy.get('button[aria-label="Essay"]').should('exist');
		cy.contains('Suggested').should('not.exist');
		cy.get('#temporary-chat-button').should('not.exist');
		cy.get('button[aria-label="Controls"]').should('not.exist');
	});

	it('formats and previews participant essay Markdown', () => {
		cy.get('textarea[data-experiment-field="essay"]').as('essay').clear();
		cy.get('button[aria-label="Bold"]').click();
		cy.get('@essay').should('have.value', '**bold text**');
		cy.contains('button', 'Preview').click();
		cy.contains('strong', 'bold text').should('be.visible');
	});

	it('restricts the profile menu to sign out', () => {
		cy.get('img[src*="/api/v1/users/"][src*="/profile/image"]').filter(':visible').last().click();
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
	(Cypress.env('researchModelFixture') ? it : it.skip)(
		'streams, stops and reloads research chat while keeping excluded execution flags off',
		() => {
			cy.get('button[aria-label="Selected model: Study model"]').should('be.visible');
			cy.intercept('POST', '**/api/chat/completions').as('completion');
			const timingEvents: string[] = [];
			cy.intercept('POST', '**/experiments/current/runtime/visible-timing', (request) => {
				expect(request.body.request_id).to.be.a('string').and.not.be.empty;
				timingEvents.push(request.body.event);
				request.reply({ status: true });
			}).as('visibleTiming');
			cy.get('#chat-input').type('Help me evaluate the evidence.{enter}');
			cy.wait('@completion').then(({ request }) => {
				expect(request.body.features.voice).to.eq(false);
				expect(request.body.features.code_interpreter).to.eq(false);
				expect(request.body).not.to.have.property('terminal_id');
			});
			cy.contains('A helpful research response with evidence.', { timeout: 30000 }).should(
				'be.visible'
			);
			cy.location('pathname').should('match', /^\/c\//);
			cy.wrap(null).should(() =>
				expect(timingEvents).to.include.members(['FIRST_VISIBLE', 'COMPLETED_VISIBLE'])
			);
			cy.get('button[aria-label="Read Aloud"]').should('not.exist');
			cy.reload();
			cy.contains('A helpful research response with evidence.', { timeout: 20000 }).should(
				'be.visible'
			);
			cy.get('#chat-input').type('STOP test response.{enter}');
			cy.wait('@completion');
			cy.intercept('POST', '**/api/tasks/chat/*/stop').as('stop');
			cy.get('#chat-input').closest('form').find('button').last().click();
			cy.wait('@stop').its('response.statusCode').should('eq', 200);
		}
	);
});
