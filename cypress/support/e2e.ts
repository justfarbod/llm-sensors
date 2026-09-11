/// <reference types="cypress" />
// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../support/index.d.ts" />

export const adminUser = {
	name: 'Admin User',
	email: 'admin@example.com',
	password: 'password'
};

export const normalUser = {
	name: 'Normal User',
	email: 'normal-user@example.com',
	password: 'password'
};

export const experimentUser = {
	name: 'Experiment User',
	email: 'experiment-user@example.com',
	password: 'password'
};

// Reuse the fixture admin session within a spec instead of consuming the signin
// rate limit each time we seed a user, group or model.
let fixtureAdminSession: { token: string } | undefined;
const getFixtureAdminSession = () => {
	if (fixtureAdminSession) return cy.wrap({ body: fixtureAdminSession }, { log: false });
	return cy
		.request('POST', '/api/v1/auths/signin', {
			email: adminUser.email,
			password: adminUser.password
		})
		.then((response) => {
			fixtureAdminSession = response.body;
			return response;
		});
};

const login = (email: string, password: string, admin = false) => {
	return cy.session(
		`${admin ? 'admin' : 'user'}-${email}`,
		() => {
			// Make sure to test against us english to have stable tests,
			// regardless on local language preferences
			localStorage.setItem('locale', 'en-US');
			// Visit auth page
			cy.visit('/auth');
			// Fill out the form
			cy.get('input[autocomplete="email"]').type(email);
			cy.get('input[type="password"]').type(password);
			// Submit the form
			cy.get('button[type="submit"]').click();
			if (admin) {
				cy.url().should('include', '/admin/analytics/overview');
				cy.contains('Researcher Dashboard').should('exist');
			} else {
				// Wait until the user is redirected to the normal chat interface.
				cy.get('#chat-search').should('exist');
				if (localStorage.getItem('version') === null) {
					cy.get('button').contains("Okay, Let's Go!").click();
				}
			}
		},
		{
			validate: () => {
				cy.request({
					method: 'GET',
					url: '/api/v1/auths/',
					headers: {
						Authorization: 'Bearer ' + localStorage.getItem('token')
					}
				});
			}
		}
	);
};

const register = (name: string, email: string, password: string) => {
	return cy
		.request({
			method: 'POST',
			url: '/api/v1/auths/signup',
			body: {
				name: name,
				email: email,
				password: password
			},
			failOnStatusCode: false
		})
		.then((response) => {
			expect(response.status).to.be.oneOf([200, 400]);
		});
};

const registerAdmin = () => {
	return cy
		.request({
			method: 'POST',
			url: '/api/v1/auths/signin',
			body: { email: adminUser.email, password: adminUser.password },
			failOnStatusCode: false
		})
		.then((response) => {
			if (response.status === 200) {
				fixtureAdminSession = response.body;
				return;
			}
			return register(adminUser.name, adminUser.email, adminUser.password);
		});
};

const ensureUserRole = (targetUser: typeof normalUser, role = 'user') => {
	// The first admin signup disables public signup. Seed other test users via
	// the authenticated admin API so a clean isolated database works as well.
	return getFixtureAdminSession().then((session) => {
		const headers = { Authorization: `Bearer ${session.body.token}` };
		return cy
			.request({
				method: 'GET',
				url: `/api/v1/users/?query=${encodeURIComponent(targetUser.email)}`,
				headers
			})
			.then((response) => {
				const existing = response.body.users.find(
					(item: { email: string }) => item.email === targetUser.email
				);
				return cy.request({
					method: 'POST',
					url: existing ? `/api/v1/users/${existing.id}/update` : '/api/v1/auths/add',
					headers,
					body: existing ? { role } : { ...targetUser, role }
				});
			});
	});
};

const registerNormalUser = () => ensureUserRole(normalUser);

const registerExperimentUser = () => {
	return ensureUserRole(experimentUser).then(() => {
		getFixtureAdminSession().then((session) => {
			const authorization = `Bearer ${session.body.token}`;
			const configureFixtureModel = (groupId: string) => {
				if (!Cypress.env('researchModelFixture')) return;
				const headers = { Authorization: authorization };
				cy.request({
					url: '/api/v1/models/model?id=study-model',
					headers,
					failOnStatusCode: false
				}).then((response) => {
					if (response.status === 200) return;
					cy.request({
						method: 'POST',
						url: '/api/v1/models/create',
						headers,
						body: {
							id: 'study-model',
							name: 'Study model',
							meta: {},
							params: {},
							access_grants: [
								{ principal_type: 'group', principal_id: groupId, permission: 'read' }
							]
						}
					});
				});
			};
			cy.request({
				method: 'GET',
				url: `/api/v1/users/?query=${encodeURIComponent(experimentUser.email)}`,
				headers: { Authorization: authorization }
			}).then((usersResponse) => {
				const participant = usersResponse.body.users.find(
					(item: { email: string; id: string }) => item.email === experimentUser.email
				);
				cy.request({
					method: 'GET',
					url: '/api/v1/groups/',
					headers: { Authorization: authorization }
				}).then((groupsResponse) => {
					const existing = groupsResponse.body.find(
						(group: { name: string }) => group.name === 'Cypress Experiment Group'
					);
					if (existing) {
						configureFixtureModel(existing.id);
						cy.request({
							method: 'POST',
							url: `/api/v1/groups/id/${existing.id}/users/add`,
							headers: { Authorization: authorization },
							body: { user_ids: [participant.id] }
						});
						return;
					}

					cy.request({
						method: 'POST',
						url: '/api/v1/essays/topics/create',
						headers: { Authorization: authorization },
						body: { title: 'Cypress Topic', question: 'Write a short research essay.' }
					}).then((topicResponse) => {
						cy.request({
							method: 'POST',
							url: '/api/v1/groups/create',
							headers: { Authorization: authorization },
							body: {
								name: 'Cypress Experiment Group',
								description: 'Cypress experiment access fixture',
								permissions: { features: { essay_sidebar: true } },
								data: {
									config: {
										experiment_mode_enabled: true,
										essay_topic_mode: 'specific',
										essay_topic_id: topicResponse.body.id
									}
								}
							}
						}).then((groupResponse) => {
							configureFixtureModel(groupResponse.body.id);
							cy.request({
								method: 'POST',
								url: `/api/v1/groups/id/${groupResponse.body.id}/users/add`,
								headers: { Authorization: authorization },
								body: { user_ids: [participant.id] }
							});
						});
					});
				});
			});
		});
	});
};

const advanceExperiment = (token: string): Cypress.Chainable => {
	const authorization = `Bearer ${token}`;
	return cy
		.request({
			method: 'GET',
			url: '/api/v1/experiments/current',
			headers: { Authorization: authorization }
		})
		.then((response) => {
			const request = (path: string, body?: object) =>
				cy.request({
					method: 'POST',
					url: `/api/v1/experiments/current/${path}`,
					headers: { Authorization: authorization },
					body
				});
			if (response.body.state === 'CONSENT_REQUIRED')
				return request('consent').then(() => advanceExperiment(token));
			if (response.body.state === 'PRE_SURVEY_REQUIRED') {
				return request('pre-survey', {
					school_class: 'Grade 10',
					ai_familiarity: 3,
					ai_schoolwork_frequency: 'Sometimes',
					essay_writing_confidence: 3,
					age_range: '15–16'
				}).then(() => advanceExperiment(token));
			}
			if (response.body.state === 'TOPIC_REQUIRED')
				return request('start').then(() => advanceExperiment(token));
			expect(response.body.state).to.eq('IN_PROGRESS');
		});
};

const loginAdmin = () => {
	return login(adminUser.email, adminUser.password, true);
};

const loginNormalUser = () => login(normalUser.email, normalUser.password);
const loginExperimentUser = () =>
	cy.session('experiment-user', () => {
		cy.request('POST', '/api/v1/auths/signin', {
			email: experimentUser.email,
			password: experimentUser.password
		}).then((session) => {
			return advanceExperiment(session.body.token).then(() => {
				localStorage.setItem('token', session.body.token);
				localStorage.setItem('locale', 'en-US');
				cy.visit('/');
				cy.get('#chat-input', { timeout: 20_000 }).should('exist');
			});
		});
	});

Cypress.Commands.add('login', (email, password) => login(email, password));
Cypress.Commands.add('register', (name, email, password) => register(name, email, password));
Cypress.Commands.add('registerAdmin', () => registerAdmin());
Cypress.Commands.add('loginAdmin', () => loginAdmin());
Cypress.Commands.add('registerNormalUser', () => registerNormalUser());
Cypress.Commands.add('loginNormalUser', () => loginNormalUser());
Cypress.Commands.add('registerExperimentUser', () => registerExperimentUser());
Cypress.Commands.add('loginExperimentUser', () => loginExperimentUser());

before(() => {
	cy.registerAdmin()
		.then(() => cy.registerNormalUser())
		.then(() => cy.registerExperimentUser());
});
