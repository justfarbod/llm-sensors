import { derived, get, writable } from 'svelte/store';
import { experimentActiveTaskId, experimentCurrent, socket } from '$lib/stores';
import { getExperimentPromptUsage } from '$lib/apis/experiments';
import { selectedPromptUsage, type TaskPromptUsage } from '$lib/utils/experimentPromptBudgets';

export const experimentQuestionContext = writable<{
	taskId: string;
	questionId: string;
	label: string;
} | null>(null);
export const experimentPromptUsage = writable<Record<string, TaskPromptUsage | null>>({});
export const activePromptBudget = derived(
	[experimentActiveTaskId, experimentCurrent, experimentQuestionContext, experimentPromptUsage],
	([$taskId, $current, $question, $usage]) => {
		const task = $current?.tasks?.find((task) => task.id === $taskId);
		if (!task || task.task_type === 'SURVEY') return null;
		const usage = $usage[task.id] ?? task.llm_prompt_usage;
		const question = $question?.taskId === task.id ? $question : null;
		return {
			taskId: task.id,
			label: usage?.mode === 'PER_QUESTION' ? question?.label : task.title,
			usage: selectedPromptUsage(usage, question?.questionId),
			blocked:
				!['ACTIVE', 'AVAILABLE', 'COMPLETED'].includes(task.status) ||
				!selectedPromptUsage(usage, question?.questionId)?.available
		};
	}
);
export const promptBudgetBlocked = derived(
	activePromptBudget,
	($budget) => $budget?.blocked ?? false
);

export const refreshPromptUsage = async (taskId = get(experimentActiveTaskId)) => {
	if (!taskId) return;
	try {
		const usage = await getExperimentPromptUsage(localStorage.token, taskId);
		experimentPromptUsage.update((values) => ({ ...values, [taskId]: usage }));
	} catch {
		// Keep the last server value. The generation endpoint remains authoritative.
	}
};

export const trackPromptUsage = () => {
	let previousSocket: any = null;
	const update = ({ task_id, usage }: { task_id: string; usage: TaskPromptUsage }) => {
		if (get(experimentCurrent)?.tasks?.some((task) => task.id === task_id))
			experimentPromptUsage.update((values) => ({ ...values, [task_id]: usage }));
	};
	const unsubscribeSocket = socket.subscribe((connection) => {
		previousSocket?.off('experiment:prompt-budget', update);
		previousSocket = connection;
		connection?.on('experiment:prompt-budget', update);
	});
	const unsubscribeTask = experimentActiveTaskId.subscribe((id) => void refreshPromptUsage(id));
	const focus = () => void refreshPromptUsage();
	window.addEventListener('focus', focus);
	// Also reconciles expired reservations and missed reconnect events.
	const timer = setInterval(focus, 10000);
	return () => {
		unsubscribeSocket();
		unsubscribeTask();
		previousSocket?.off('experiment:prompt-budget', update);
		window.removeEventListener('focus', focus);
		clearInterval(timer);
		experimentQuestionContext.set(null);
		experimentPromptUsage.set({});
	};
};
