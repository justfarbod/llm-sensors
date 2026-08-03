import { describe, expect, it } from 'vitest';

import {
	allowsPromptSuggestions,
	appAccessRedirect,
	createExperimentStateLoader,
	experimentAllowsApp,
	experimentNeedsGate,
	firstUnlockedExperimentTask,
	isExperimentParticipant,
	sameExperimentState,
	shouldScheduleEssayReminder,
	shouldRefreshExperiment
} from './experiments';

describe('experiment task selection', () => {
	it('selects the first task as soon as a prefix survey unlocks it', () => {
		const tasks = [
			{ id: 'survey', task_type: 'SURVEY', position: 0, title: 'Pre', status: 'FINALIZED' },
			{ id: 'first', task_type: 'QUESTION', position: 1, title: 'First', status: 'ACTIVE' },
			{ id: 'second', task_type: 'ESSAY', position: 2, title: 'Second', status: 'LOCKED' }
		] as const;

		expect(
			firstUnlockedExperimentTask([...tasks].filter((task) => task.task_type !== 'SURVEY'))?.id
		).toBe('first');
	});

	it('does not select a task while every non-survey stage is locked', () => {
		expect(
			firstUnlockedExperimentTask([
				{ id: 'first', task_type: 'QUESTION', position: 1, title: 'First', status: 'LOCKED' }
			])
		).toBeUndefined();
	});
});

describe('experiment and admin access policies', () => {
	const activeExperiment = { state: 'IN_PROGRESS' } as const;
	const normal = { role: 'user' };
	const admin = { role: 'admin' };

	it('keeps normal users on existing routes with prompt suggestions', () => {
		expect(appAccessRedirect('/workspace', normal, { state: 'NOT_APPLICABLE' })).toBeNull();
		expect(allowsPromptSuggestions(normal, { state: 'NOT_APPLICABLE' })).toBe(true);
	});

	it('limits experiment participants to chat routes and disables prompt suggestions', () => {
		expect(isExperimentParticipant(normal, activeExperiment)).toBe(true);
		expect(appAccessRedirect('/settings', normal, activeExperiment)).toBe('/');
		expect(appAccessRedirect('/c/chat-id', normal, activeExperiment)).toBeNull();
		expect(allowsPromptSuggestions(normal, activeExperiment)).toBe(false);
	});

	it('always treats admins as admin-only, even with an experiment state', () => {
		expect(isExperimentParticipant(admin, activeExperiment)).toBe(false);
		expect(appAccessRedirect('/c/chat-id', admin, activeExperiment)).toBe(
			'/admin/analytics/overview'
		);
		expect(appAccessRedirect('/admin/settings', admin, activeExperiment)).toBeNull();
		expect(allowsPromptSuggestions(admin, activeExperiment)).toBe(false);
	});
});

describe('experimentAllowsApp', () => {
	it('allows normal and active writing sessions', () => {
		expect(experimentAllowsApp({ state: 'NOT_APPLICABLE' })).toBe(true);
		expect(experimentAllowsApp({ state: 'IN_PROGRESS' })).toBe(true);
	});

	it('blocks every required experiment step', () => {
		expect(experimentAllowsApp({ state: 'CONSENT_REQUIRED' })).toBe(false);
		expect(experimentAllowsApp({ state: 'POST_SURVEY_REQUIRED' })).toBe(false);
		expect(experimentAllowsApp({ state: 'COMPLETED' })).toBe(false);
		expect(experimentAllowsApp(undefined)).toBe(false);
	});
});

describe('experiment state loader', () => {
	it('does not load again after NOT_APPLICABLE', async () => {
		const loadState = createExperimentStateLoader();
		let calls = 0;
		const result = await loadState({ state: 'NOT_APPLICABLE' }, async () => {
			calls += 1;
			return { state: 'CONSENT_REQUIRED' };
		});

		expect(result).toEqual({ state: 'NOT_APPLICABLE' });
		expect(calls).toBe(0);
	});

	it('deduplicates concurrent initial requests', async () => {
		const loadState = createExperimentStateLoader();
		let calls = 0;
		const load = async () => {
			calls += 1;
			await Promise.resolve();
			return { state: 'NOT_APPLICABLE' } as const;
		};

		const [first, second] = await Promise.all([
			loadState(undefined, load),
			loadState(undefined, load)
		]);
		expect(first).toEqual(second);
		expect(calls).toBe(1);
	});

	it('recognizes unchanged minimal state DTOs', () => {
		expect(sameExperimentState({ state: 'NOT_APPLICABLE' }, { state: 'NOT_APPLICABLE' })).toBe(
			true
		);
		expect(
			sameExperimentState(
				{ state: 'IN_PROGRESS', session_id: 'a' },
				{ state: 'IN_PROGRESS', session_id: 'b' }
			)
		).toBe(false);
	});
});

describe('experiment refresh behavior', () => {
	it('treats NOT_APPLICABLE as a terminal no-op', () => {
		expect(shouldRefreshExperiment({ state: 'NOT_APPLICABLE' })).toBe(false);
		expect(experimentNeedsGate({ state: 'NOT_APPLICABLE' })).toBe(false);
	});

	it('allows initial and active-session refreshes', () => {
		expect(shouldRefreshExperiment(undefined)).toBe(true);
		expect(shouldRefreshExperiment({ state: 'CONSENT_REQUIRED' })).toBe(true);
		expect(experimentNeedsGate({ state: 'CONSENT_REQUIRED' })).toBe(true);
	});
});

describe('essay reminder behavior', () => {
	it('schedules only while an experiment writer has closed the essay sidebar', () => {
		expect(shouldScheduleEssayReminder({ state: 'IN_PROGRESS' }, false)).toBe(true);
		expect(shouldScheduleEssayReminder({ state: 'IN_PROGRESS' }, true)).toBe(false);
		expect(shouldScheduleEssayReminder({ state: 'NOT_APPLICABLE' }, false)).toBe(false);
		expect(shouldScheduleEssayReminder({ state: 'POST_SURVEY_REQUIRED' }, false)).toBe(false);
	});
});
