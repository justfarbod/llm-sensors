import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ExperimentState =
	| 'NOT_APPLICABLE'
	| 'CONSENT_REQUIRED'
	| 'PRE_SURVEY_REQUIRED'
	| 'TOPIC_REQUIRED'
	| 'TASK_REQUIRED'
	| 'IN_PROGRESS'
	| 'POST_SURVEY_REQUIRED'
	| 'THANK_YOU_REQUIRED'
	| 'COMPLETED'
	| 'CONFIGURATION_ERROR';

export type ExperimentCurrent = {
	state: ExperimentState;
	session_id?: string | null;
	group_id?: string | null;
	plan_id?: string | null;
	progression_mode?: 'STRICT_SEQUENTIAL' | 'FREE_NAVIGATION' | 'SEQUENTIAL_REVIEW' | null;
	chat_mode?: 'SHARED_EXPERIMENT' | 'FRESH_PER_TASK' | null;
	survey_variant?: 'ESSAY' | 'TASK_NEUTRAL' | null;
	tasks?: ExperimentTaskSummary[];
	topic?: { id: string; title: string; question: string } | null;
	agreement_text?: string | null;
	error?: string | null;
};

export type ExperimentTaskSummary = {
	id: string;
	position: number;
	task_type: 'ESSAY' | 'QUESTION' | 'SURVEY';
	title: string;
	status: 'LOCKED' | 'AVAILABLE' | 'ACTIVE' | 'COMPLETED' | 'FINALIZED' | 'SKIPPED';
	question_count?: number | null;
	essay_topic?: { id: string; title: string; question: string } | null;
	survey_required?: boolean | null;
};

const request = async <T = ExperimentCurrent>(
	token: string,
	path = '',
	options: RequestInit = {}
) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/experiments/current${path}`, {
		...options,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`,
			...(options.headers ?? {})
		}
	});
	const body = await response.json().catch(() => null);
	if (!response.ok) throw body?.detail ?? `Request failed with status ${response.status}`;
	return body as T;
};

export const getCurrentExperiment = (token: string) => request(token);
export const submitExperimentConsent = (token: string) =>
	request(token, '/consent', { method: 'POST' });
export const submitExperimentPreSurvey = (token: string, body: object) =>
	request(token, '/pre-survey', { method: 'POST', body: JSON.stringify(body) });
export const startExperiment = (token: string) => request(token, '/start', { method: 'POST' });
export const submitExperimentPostSurvey = (token: string, body: object) =>
	request(token, '/post-survey', { method: 'POST', body: JSON.stringify(body) });
export const completeExperiment = (token: string) =>
	request(token, '/complete', { method: 'POST' });
export const getExperimentTask = (token: string, taskId: string) =>
	request<any>(token, `/tasks/${taskId}`);
export const saveEssayTaskDraft = (token: string, taskId: string, content: string) =>
	request<any>(token, `/tasks/${taskId}/essay-draft`, {
		method: 'PATCH',
		body: JSON.stringify({ content })
	});
export const saveQuestionTaskDraft = (token: string, taskId: string, answers: any[]) =>
	request<any>(token, `/tasks/${taskId}/question-draft`, {
		method: 'PUT',
		body: JSON.stringify({ answers })
	});
export const completeExperimentTask = (token: string, taskId: string) =>
	request(token, `/tasks/${taskId}/complete`, { method: 'POST' });
export const finalizeEssayTask = (token: string, taskId: string, content: string) =>
	request<any>(token, `/tasks/${taskId}/finalize/essay`, {
		method: 'POST',
		body: JSON.stringify({ content })
	});
export const finalizeQuestionTask = (token: string, taskId: string, answers: any[]) =>
	request<any>(token, `/tasks/${taskId}/finalize/questions`, {
		method: 'POST',
		body: JSON.stringify({ answers })
	});
export const finalizeExperimentTasks = (token: string) =>
	request(token, '/finalize-tasks', { method: 'POST' });
export const submitExperimentSurvey = (token: string, taskId: string, answers: any[]) =>
	request<any>(token, `/tasks/${taskId}/survey`, {
		method: 'POST',
		body: JSON.stringify({ answers })
	});
export const skipExperimentSurvey = (token: string, taskId: string) =>
	request<any>(token, `/tasks/${taskId}/skip-survey`, { method: 'POST' });
