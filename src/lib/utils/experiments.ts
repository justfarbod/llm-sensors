import type { ExperimentCurrent } from '$lib/apis/experiments';

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
	left?.agreement_text === right?.agreement_text &&
	left?.error === right?.error &&
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
