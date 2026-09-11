/// <reference types="cypress" />
const experimentUser = { email: 'experiment-user@example.com', password: 'password' };
const normalUser = { email: 'normal-user@example.com', password: 'password' };

// These assertions use the real frontend/auth against an isolated test backend.
// Workflow responses and the extension bridge are controlled at their external
// boundaries; no production code is altered to bypass experiment gating.
const currentUrl = '**/api/v1/experiments/current';
const topic = {
	id: 'profile-topic',
	title: 'Research writing',
	question: 'Explain your reasoning.'
};
const telemetry = {
	required: true,
	ready: true,
	minimum_version: '2.0.0',
	schema_version: 2,
	extension_id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
	configuration_error: null
};

function visitAs(
	account: typeof normalUser,
	url = '/',
	beforeLoad?: (win: Cypress.AUTWindow) => void
) {
	cy.request('POST', '/api/v1/auths/signin', {
		email: account.email,
		password: account.password
	}).then(({ body }) => {
		cy.visit(url, {
			onBeforeLoad(win) {
				win.localStorage.setItem('token', body.token);
				win.localStorage.setItem('locale', 'en-US');
				beforeLoad?.(win);
			}
		});
	});
}

function extensionBridge(win: Cypress.AUTWindow, ready = true) {
	win.addEventListener('open-webui-experiment-extension-check-request', ((event: CustomEvent) => {
		win.dispatchEvent(
			new win.CustomEvent('open-webui-experiment-extension-check-result', {
				detail: { ...event.detail, ready }
			})
		);
	}) as EventListener);
	win.addEventListener('open-webui-experiment-telemetry-flush-request', ((event: CustomEvent) => {
		win.dispatchEvent(
			new win.CustomEvent('open-webui-experiment-telemetry-flush-result', {
				detail: { request_id: event.detail.request_id, ok: true }
			})
		);
	}) as EventListener);
}

describe('Research profile boundaries and experiment regression', () => {
	beforeEach(() => {
		cy.viewport(1440, 1000);
	});

	it('ignores saved voice/terminal selections, preserves the saved value and removes shortcuts', () => {
		cy.intercept('GET', '**/api/v1/users/user/settings', {
			ui: {
				responseAutoPlayback: true,
				audio: { tts: { engine: 'browser-kokoro' } },
				terminalServers: [{ url: 'http://unused-terminal.invalid', enabled: true }]
			}
		});
		cy.intercept('**/*unused-terminal*', () => {
			throw new Error('Research discovered a terminal');
		});
		visitAs(normalUser, '/?call=true&code-interpreter=true', (win) => {
			win.localStorage.selectedTerminalId = 'http://unused-terminal.invalid';
		});
		cy.get('#chat-input', { timeout: 20000 }).should('exist');
		for (const label of ['Dictate', 'Voice mode', 'Code Interpreter']) {
			cy.get(`button[aria-label="${label}"]`).should('not.exist');
		}
		for (const route of ['notes', 'channels', 'calendar', 'automations', 'playground']) {
			cy.get(`a[href="/${route}"]`).should('not.exist');
		}
		cy.window().then((win) =>
			expect(win.localStorage.selectedTerminalId).to.eq('http://unused-terminal.invalid')
		);
	});

	it('redirects excluded deep links while retaining the authentication gate', () => {
		cy.visit('/notes/old-saved-note');
		cy.location('pathname').should('eq', '/auth');
		cy.get('input[autocomplete="email"]').should('be.visible');
		visitAs(normalUser, '/notes/old-saved-note');
		cy.location('pathname').should('eq', '/');
		cy.get('#chat-input').should('be.visible');
		for (const route of ['channels', 'calendar', 'automations', 'playground']) {
			cy.visit(`/${route}/old-link`);
			cy.location('pathname').should('eq', '/');
			cy.get('#chat-input').should('be.visible');
		}
	});

	it('keeps consent, pre-survey and required extension gating before legacy writing', () => {
		let state: any = {
			state: 'CONSENT_REQUIRED',
			session_id: 'gate-session',
			agreement_text: 'Study consent',
			topic,
			telemetry_extension: telemetry
		};
		cy.intercept('GET', currentUrl, (req) => req.reply(state));
		cy.intercept('POST', '**/experiments/current/consent', (req) => {
			state = { ...state, state: 'PRE_SURVEY_REQUIRED' };
			req.reply(state);
		}).as('consent');
		cy.intercept('POST', '**/experiments/current/pre-survey', (req) => {
			expect(req.body).to.include({ school_class: 'Grade 10', ai_familiarity: 3 });
			state = { ...state, state: 'TOPIC_REQUIRED' };
			req.reply(state);
		}).as('survey');
		cy.intercept('POST', '**/experiments/current/start', (req) => {
			state = { ...state, state: 'IN_PROGRESS' };
			req.reply(state);
		}).as('start');
		visitAs(experimentUser);
		cy.get('#chat-input').should('not.exist');
		cy.contains('button', 'Continue').should('be.disabled');
		cy.get('input[type="checkbox"]').check();
		cy.contains('button', 'Continue').click();
		cy.wait('@consent');
		cy.contains('label', 'school class').find('input').type('Grade 10');
		cy.contains('label', 'How familiar').find('select').select('3');
		cy.contains('label', 'How often').find('select').select('Sometimes');
		cy.contains('label', 'How confident').find('select').select('3');
		cy.contains('label', 'Which age').find('select').select('15–16');
		cy.contains('button', 'Submit Pre-Survey').click();
		cy.wait('@survey');
		// Server-ready alone is insufficient: the extension must answer in this tab.
		cy.contains('button', 'Start Writing').should('be.disabled');
		cy.get('#chat-input').should('not.exist');
		cy.reload();
		// Reload with a responsive test extension bridge, preserving the server state.
		visitAs(experimentUser, '/', extensionBridge);
		cy.contains('button', 'Start Writing', { timeout: 20000 }).should('be.enabled').click();
		cy.wait('@start');
		cy.get('#chat-input').should('exist');
		cy.get('button[aria-label="Essay"]').should('exist');
	});

	it('flushes telemetry before legacy essay submission and retains post-survey completion', () => {
		let state: any = {
			state: 'IN_PROGRESS',
			session_id: 'legacy-session',
			topic,
			telemetry_extension: telemetry
		};
		let flushed = false;
		cy.intercept('GET', currentUrl, (req) => req.reply(state));
		cy.intercept('GET', '**/essays/workspace', { topic, latest_essay: null });
		cy.intercept('POST', '**/essays/submit', (req) => {
			expect(flushed).to.eq(true);
			expect(req.body.content).to.eq('My research essay.');
			state = { ...state, state: 'POST_SURVEY_REQUIRED' };
			req.reply({ id: 'essay-1' });
		}).as('essay');
		cy.intercept('POST', '**/experiments/current/post-survey', (req) => {
			expect(req.body.ai_helpfulness).to.eq(3);
			state = { ...state, state: 'THANK_YOU_REQUIRED' };
			req.reply(state);
		}).as('postSurvey');
		visitAs(experimentUser, '/', (win) => {
			extensionBridge(win);
			win.addEventListener('open-webui-experiment-telemetry-flush-request', () => {
				flushed = true;
			});
		});
		cy.get('#chat-input').should('be.focused');
		cy.get('textarea[data-experiment-field="essay"]').clear().type('My research essay.');
		cy.contains('button', 'Submit Essay').click();
		cy.wait('@essay');
		cy.contains('h1', 'Post-Survey').should('exist');
		cy.get('form select').each(($select) => {
			const values = [...$select[0].querySelectorAll('option')]
				.map((option) => option.value)
				.filter(Boolean);
			const value = values.includes('3') ? '3' : values[0];
			cy.wrap($select).select(value).should('have.value', value);
		});
		cy.contains('button', 'Submit Post-Survey').click();
		cy.wait('@postSurvey');
		cy.contains('Thank').should('exist');
	});
	it('preserves plan task editing, telemetry flush, progression and task-scoped history', () => {
		const essay = {
			id: 'task-essay',
			task_type: 'ESSAY',
			title: 'First essay',
			position: 0,
			status: 'ACTIVE'
		};
		const questions = {
			id: 'task-questions',
			task_type: 'QUESTION',
			title: 'Reasoning',
			position: 1,
			status: 'LOCKED'
		};
		let state: any = {
			state: 'IN_PROGRESS',
			session_id: 'plan-session',
			plan_id: 'plan-1',
			progression_mode: 'STRICT_SEQUENTIAL',
			chat_mode: 'FRESH_PER_TASK',
			tasks: [essay, questions],
			telemetry_extension: telemetry
		};
		let flushes = 0;
		const scopes: string[] = [];
		cy.intercept('GET', currentUrl, (req) => req.reply(state));
		cy.intercept('GET', '**/api/v1/chats/?*', (req) => {
			const scope = new URL(req.url).searchParams.get('experiment_session_task_id');
			if (scope) scopes.push(scope);
			req.reply([]);
		});
		cy.intercept('GET', '**/experiments/current/tasks/task-essay', {
			...essay,
			essay_topic: topic,
			draft: ''
		});
		cy.intercept('PATCH', '**/tasks/task-essay/essay-draft', (req) =>
			req.reply({ content: req.body.content })
		);
		cy.intercept('POST', '**/tasks/task-essay/finalize/essay', (req) => {
			expect(flushes).to.be.greaterThan(0);
			expect(req.body.content).to.eq('Plan essay answer.');
			state = {
				...state,
				tasks: [
					{ ...essay, status: 'FINALIZED' },
					{ ...questions, status: 'ACTIVE' }
				]
			};
			req.reply(state);
		}).as('finalizeEssay');
		cy.intercept('GET', '**/experiments/current/tasks/task-questions', {
			...questions,
			status: 'ACTIVE',
			question_task: {
				questions: [
					{
						id: 'q1',
						title: 'Explain',
						description: 'Why?',
						question_type: 'FREE_TEXT',
						max_score: 5
					}
				]
			},
			submission: { id: 'submission-1', responses: [] }
		});
		cy.intercept('PUT', '**/tasks/task-questions/question-draft', (req) =>
			req.reply({ answers: req.body.answers })
		);
		cy.intercept('POST', '**/tasks/task-questions/finalize/questions', (req) => {
			expect(flushes).to.be.greaterThan(1);
			expect(req.body.answers[0]).to.include({
				question_id: 'q1',
				text: 'Because of the evidence.'
			});
			state = { ...state, state: 'POST_SURVEY_REQUIRED', survey_variant: 'TASK_NEUTRAL' };
			req.reply({ experiment: state });
		}).as('finalizeQuestions');
		visitAs(experimentUser, '/', (win) => {
			extensionBridge(win);
			win.addEventListener('open-webui-experiment-telemetry-flush-request', () => {
				flushes++;
			});
		});
		cy.get('#chat-input').should('be.focused');
		// The editor applies focus again after asynchronous chat initialization.
		// Let that startup focus settle before simulating typing in another pane.
		cy.wait(500);
		cy.contains('button', 'Reasoning').should('be.disabled');
		cy.get('textarea[data-experiment-field="essay"]')
			.clear()
			.type('Plan essay answer.')
			.should('have.value', 'Plan essay answer.');
		cy.contains('button', 'Submit task').click();
		cy.wait('@finalizeEssay');
		cy.get('textarea[data-question-control="free_text"]').type('Because of the evidence.');
		cy.contains('button', 'Submit task').click();
		cy.wait('@finalizeQuestions');
		cy.then(() => expect(scopes).to.include.members(['task-essay', 'task-questions']));
		cy.contains('How satisfied are you with the work you submitted?').should('exist');
	});
});
