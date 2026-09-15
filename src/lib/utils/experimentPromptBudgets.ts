export type LLMPromptBudget = {
	mode: 'TASK' | 'PER_QUESTION';
	limit: number | null;
	question_limits: Record<string, number>;
};
export type PromptUsage = {
	limit: number;
	used: number;
	pending: number;
	remaining: number;
	available: number;
};
export type TaskPromptUsage = {
	mode: LLMPromptBudget['mode'];
	scopes: Record<string, PromptUsage>;
};
export const defaultPromptBudget = (): LLMPromptBudget => ({
	mode: 'TASK',
	limit: 100,
	question_limits: {}
});
export const questionPromptBudget = (questions: { id?: string }[]): LLMPromptBudget => ({
	mode: 'PER_QUESTION',
	limit: null,
	question_limits: Object.fromEntries(questions.filter((q) => q.id).map((q) => [q.id!, 100]))
});
export const selectedPromptUsage = (
	usage: TaskPromptUsage | null | undefined,
	questionId?: string | null
) => usage?.scopes[usage.mode === 'TASK' ? 'TASK' : (questionId ?? '')] ?? null;
