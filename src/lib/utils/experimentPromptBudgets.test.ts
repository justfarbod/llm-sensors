import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import {
	defaultPromptBudget,
	questionPromptBudget,
	selectedPromptUsage,
	type TaskPromptUsage
} from './experimentPromptBudgets';

vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		experimentActiveTaskId: writable(null),
		experimentCurrent: writable(null),
		socket: writable(null)
	};
});
vi.mock('$lib/apis/experiments', () => ({ getExperimentPromptUsage: vi.fn() }));
import { experimentActiveTaskId, experimentCurrent } from '$lib/stores';
import {
	experimentQuestionContext,
	experimentPromptUsage,
	activePromptBudget,
	promptBudgetBlocked
} from '$lib/stores/experimentPromptBudgets';

const count = (limit: number, used = 0, pending = 0) => ({
	limit,
	used,
	pending,
	remaining: limit - used,
	available: limit - used - pending
});
const perQuestion: TaskPromptUsage = {
	mode: 'PER_QUESTION',
	scopes: { q1: count(0), q2: count(3, 1), q3: count(1, 0, 1) }
};

beforeEach(() => {
	experimentActiveTaskId.set(null);
	experimentCurrent.set(null);
	experimentQuestionContext.set(null);
	experimentPromptUsage.set({});
});

describe('prompt budget authoring', () => {
	it('defaults each task and question to independent values of 100', () => {
		expect(defaultPromptBudget()).toEqual({ mode: 'TASK', limit: 100, question_limits: {} });
		const budget = questionPromptBudget([{ id: 'q1' }, { id: 'q2' }]);
		budget.question_limits.q1 = 5;
		expect(budget.question_limits).toEqual({ q1: 5, q2: 100 });
	});
	it('does not substitute another question when context is absent or invalid', () => {
		expect(selectedPromptUsage(perQuestion)).toBeNull();
		expect(selectedPromptUsage(perQuestion, 'foreign')).toBeNull();
		expect(selectedPromptUsage(perQuestion, 'q2')?.remaining).toBe(2);
	});
});

describe('student prompt allowance', () => {
	const select = () => {
		experimentCurrent.set({
			state: 'IN_PROGRESS',
			tasks: [
				{
					id: 'task',
					position: 0,
					task_type: 'QUESTION',
					title: 'Quiz',
					status: 'ACTIVE',
					llm_prompt_usage: perQuestion
				}
			]
		});
		experimentActiveTaskId.set('task');
	};
	it('blocks while selecting a question and uses the selected question budget', () => {
		select();
		expect(get(promptBudgetBlocked)).toBe(true);
		experimentQuestionContext.set({ taskId: 'task', questionId: 'q1', label: 'Question 1' });
		expect(get(promptBudgetBlocked)).toBe(true);
		experimentQuestionContext.set({ taskId: 'task', questionId: 'q2', label: 'Question 2' });
		expect(get(promptBudgetBlocked)).toBe(false);
		expect(get(activePromptBudget)?.usage?.remaining).toBe(2);
	});
	it('blocks reserved allowance and restores it after cancellation', () => {
		select();
		experimentQuestionContext.set({ taskId: 'task', questionId: 'q3', label: 'Question 3' });
		expect(get(promptBudgetBlocked)).toBe(true);
		experimentPromptUsage.set({
			task: { ...perQuestion, scopes: { ...perQuestion.scopes, q3: count(1) } }
		});
		expect(get(promptBudgetBlocked)).toBe(false);
	});
	it('never carries a question context into a different task', () => {
		select();
		experimentQuestionContext.set({ taskId: 'other', questionId: 'q2', label: 'Other' });
		expect(get(promptBudgetBlocked)).toBe(true);
	});
	it('uses a task-wide budget across questions and does not affect ordinary chat', () => {
		expect(get(promptBudgetBlocked)).toBe(false);
		select();
		experimentPromptUsage.set({ task: { mode: 'TASK', scopes: { TASK: count(100, 10) } } });
		expect(get(promptBudgetBlocked)).toBe(false);
		expect(get(activePromptBudget)?.usage?.remaining).toBe(90);
	});
});
