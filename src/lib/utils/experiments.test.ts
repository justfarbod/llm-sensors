import { describe, expect, it } from 'vitest';
import type { ExperimentTaskSummary } from '$lib/apis/experiments';

import {
	allowsPromptSuggestions,
	appAccessRedirect,
	createExperimentStateLoader,
	experimentAllowsApp,
	experimentNeedsGate,
	experimentTaskAfterSurvey,
	firstUnlockedExperimentTask,
	isExperimentParticipant,
	sameExperimentState,
	shouldScheduleEssayReminder,
	shouldRefreshExperiment
} from './experiments';

describe('experiment task selection', () => {
	const betweenSurvey: ExperimentTaskSummary[] = [
		{ id: 'essay', task_type: 'ESSAY', position: 0, title: 'Essay', status: 'FINALIZED' },
		{ id: 'survey', task_type: 'SURVEY', position: 1, title: 'Survey', status: 'ACTIVE' },
		{ id: 'question', task_type: 'QUESTION', position: 2, title: 'Question', status: 'LOCKED' }
	];

	it.each(['FINALIZED', 'SKIPPED'] as const)(
		'advances from the submitted essay to questions when the survey is %s',
		(status) => {
			const afterSurvey: ExperimentTaskSummary[] = [
				betweenSurvey[0],
				{ ...betweenSurvey[1], status },
				{ ...betweenSurvey[2], status: 'ACTIVE' }
			];
			expect(experimentTaskAfterSurvey(betweenSurvey, afterSurvey)?.id).toBe('question');
			// Subsequent refreshes must allow the participant to review earlier tasks.
			expect(experimentTaskAfterSurvey(afterSurvey, afterSurvey)).toBeUndefined();
		}
	);

	it('selects an available task after a survey unlocks a task block', () => {
		expect(
			experimentTaskAfterSurvey(betweenSurvey, [
				{ ...betweenSurvey[0], status: 'COMPLETED' },
				{ ...betweenSurvey[1], status: 'FINALIZED' },
				{ ...betweenSurvey[2], status: 'AVAILABLE' }
			])?.id
		).toBe('question');
	});

	it('waits while the survey is still active', () => {
		expect(experimentTaskAfterSurvey(betweenSurvey, betweenSurvey)).toBeUndefined();
	});

	it('waits for consecutive surveys before advancing to the next task', () => {
		const nextSurvey: ExperimentTaskSummary[] = [
			betweenSurvey[0],
			{ ...betweenSurvey[1], status: 'FINALIZED' },
			{ ...betweenSurvey[1], id: 'second-survey', position: 2 },
			{ ...betweenSurvey[2], position: 3 }
		];
		expect(experimentTaskAfterSurvey(betweenSurvey, nextSurvey)).toBeUndefined();
		expect(
			experimentTaskAfterSurvey(nextSurvey, [
				...nextSurvey.slice(0, 2),
				{ ...nextSurvey[2], status: 'FINALIZED' },
				{ ...nextSurvey[3], status: 'ACTIVE' }
			])?.id
		).toBe('question');
	});

	it('does not select an earlier task after the final survey', () => {
		expect(
			experimentTaskAfterSurvey(betweenSurvey.slice(0, 2), [
				betweenSurvey[0],
				{ ...betweenSurvey[1], status: 'FINALIZED' }
			])
		).toBeUndefined();
	});

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
