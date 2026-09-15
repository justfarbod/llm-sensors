import type { ExperimentCurrent, ExperimentTaskSummary } from '$lib/apis/experiments';

export const firstUnlockedExperimentTask = (tasks: ExperimentTaskSummary[]) =>
	tasks.find((task) => task.status === 'ACTIVE') ??
	tasks.find((task) => task.status === 'AVAILABLE') ??
	tasks.find((task) => task.status === 'COMPLETED') ??
	tasks.find((task) => task.status === 'FINALIZED');

export const experimentTaskAfterSurvey = (
	previousTasks: ExperimentTaskSummary[],
	tasks: ExperimentTaskSummary[]
) => {
	const survey = previousTasks.find(
		(task) => task.task_type === 'SURVEY' && task.status === 'ACTIVE'
	);
	if (
		!survey ||
		!tasks.some(
			(task) => task.id === survey.id && ['FINALIZED', 'SKIPPED'].includes(task.status)
		) ||
		tasks.some((task) => task.task_type === 'SURVEY' && task.status === 'ACTIVE')
	)
		return;

	const followingTasks = tasks.filter(
		(task) => task.task_type !== 'SURVEY' && task.position > survey.position
	);
	return (
		followingTasks.find((task) => task.status === 'ACTIVE') ??
		followingTasks.find((task) => task.status === 'AVAILABLE')
	);
};

export const experimentAllowsApp = (experiment: ExperimentCurrent | undefined) =>
	experiment !== undefined && ['NOT_APPLICABLE', 'IN_PROGRESS'].includes(experiment.state);

export const isExperimentParticipant = (
	user: { role?: string } | null | undefined,
	experiment: ExperimentCurrent | undefined
) => user?.role !== 'admin' && experiment !== undefined && experiment.state !== 'NOT_APPLICABLE';

export const appAccessRedirect = (
	pathname: string,
	user: { role?: string } | null | undefined,
	experiment: ExperimentCurrent | undefined
) => {
	if (user?.role === 'admin') {
		return pathname.startsWith('/admin') ? null : '/admin/analytics/overview';
	}
	if (isExperimentParticipant(user, experiment)) {
		return pathname === '/' || pathname.startsWith('/c/') ? null : '/';
	}
	return null;
};

export const allowsPromptSuggestions = (
	user: { role?: string } | null | undefined,
	experiment: ExperimentCurrent | undefined
) => user?.role !== 'admin' && !isExperimentParticipant(user, experiment);

export const experimentNeedsGate = (experiment: ExperimentCurrent | undefined) =>
	experiment !== undefined && !experimentAllowsApp(experiment);

export const shouldScheduleEssayReminder = (
	experiment: ExperimentCurrent | undefined,
	essaySidebarOpen: boolean
) => experiment?.state === 'IN_PROGRESS' && !essaySidebarOpen;

export const shouldRefreshExperiment = (experiment: ExperimentCurrent | undefined) =>
	experiment?.state !== 'NOT_APPLICABLE';

export const sameExperimentState = (
	left: ExperimentCurrent | undefined,
	right: ExperimentCurrent | undefined
) =>
	left?.state === right?.state &&
	left?.session_id === right?.session_id &&
	left?.group_id === right?.group_id &&
	left?.plan_id === right?.plan_id &&
	left?.progression_mode === right?.progression_mode &&
	left?.chat_mode === right?.chat_mode &&
	left?.survey_variant === right?.survey_variant &&
	JSON.stringify(left?.tasks ?? []) === JSON.stringify(right?.tasks ?? []) &&
	left?.agreement_text === right?.agreement_text &&
	left?.error === right?.error &&
	JSON.stringify(left?.telemetry_extension ?? null) ===
		JSON.stringify(right?.telemetry_extension ?? null) &&
	left?.topic?.id === right?.topic?.id &&
	left?.topic?.title === right?.topic?.title &&
	left?.topic?.question === right?.topic?.question;

export const createExperimentStateLoader = () => {
	let inFlight: Promise<ExperimentCurrent> | null = null;

	return async (
		current: ExperimentCurrent | undefined,
		load: () => Promise<ExperimentCurrent>,
		force = false
	) => {
		if (!force && !shouldRefreshExperiment(current)) return current;
		if (inFlight) return inFlight;

		inFlight = load().finally(() => {
			inFlight = null;
		});
		return inFlight;
	};
};
