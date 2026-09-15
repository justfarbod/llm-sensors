import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ResearchFilters = {
	workflow_id?: string;
	configuration_id?: string;
	condition_key?: string;
	data_kind?: string;
	group_id?: string;
	topic_id?: string;
	date_from?: number | string;
	date_to?: number | string;
	state?: string;
	completed?: string | boolean;
	page?: number;
	limit?: number;
	search?: string;
	order_by?: string;
	direction?: 'asc' | 'desc';
};

export type FullSessionExportDownload = {
	blob: Blob;
	filename: string;
};

const baseUrl = `${WEBUI_API_BASE_URL}/analytics/experiments`;

const queryString = (filters: ResearchFilters = {}) => {
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(filters)) {
		if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
	}
	return params.toString();
};

const request = async (
	token: string,
	path: string,
	filters: ResearchFilters = {},
	signal?: AbortSignal
) => {
	const query = queryString(filters);
	const response = await fetch(`${baseUrl}${path}${query ? `?${query}` : ''}`, {
		headers: { Accept: 'application/json', authorization: `Bearer ${token}` },
		signal
	});
	const body = await response.json().catch(() => null);
	if (!response.ok) throw body?.detail ?? `Request failed with status ${response.status}`;
	return body;
};

const mutate = async (token: string, path: string, method: 'POST' | 'PUT', body?: object) => {
	const response = await fetch(`${baseUrl}${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: body ? JSON.stringify(body) : undefined
	});
	const payload = await response.json().catch(() => null);
	if (!response.ok) throw payload?.detail ?? `Request failed with status ${response.status}`;
	return payload;
};

export const getResearchFilters = (token: string, signal?: AbortSignal) =>
	request(token, '/filters', {}, signal);

export const getResearchSection = (
	token: string,
	section: string,
	filters: ResearchFilters = {},
	signal?: AbortSignal
) => request(token, `/${section}`, filters, signal);

export const getResearchSession = (token: string, sessionId: string, signal?: AbortSignal) =>
	request(token, `/sessions/${sessionId}`, {}, signal);

export const getResearchTabActivity = (
	token: string,
	sessionId: string,
	filters: ResearchFilters & {
		event_type?: string;
		browser_tab_id?: number | string;
		browser_window_id?: number | string;
	} = {},
	signal?: AbortSignal
) => request(token, `/sessions/${sessionId}/tab-activity`, filters, signal);

export const getQuestionSubmission = (token: string, submissionId: string, signal?: AbortSignal) =>
	request(token, `/question-submissions/${submissionId}`, {}, signal);

export const overrideQuestionScore = (
	token: string,
	responseId: string,
	score: number,
	note?: string
) => mutate(token, `/question-responses/${responseId}/score`, 'PUT', { score, note });

export const retryQuestionGrading = (token: string, responseId: string) =>
	mutate(token, `/question-responses/${responseId}/retry`, 'POST');

export const exportFullResearchSessions = async (
	token: string,
	ids: string[],
	anonymized: boolean
): Promise<FullSessionExportDownload> => {
	const response = await fetch(`${baseUrl}/export/sessions`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ ids, anonymized })
	});
	if (!response.ok) {
		const body = await response.json().catch(() => null);
		const detail = body?.detail;
		throw (
			(typeof detail === 'string' ? detail : detail?.message) ??
			`Export failed with status ${response.status}`
		);
	}
	const disposition = response.headers.get('Content-Disposition') ?? '';
	const filename =
		disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? 'experiment-full-sessions.json';
	return { blob: await response.blob(), filename };
};

export type WorkflowConfiguration = {
	id: string;
	source_revision: number | null;
	origin: string;
	created_at: number;
	runs: number;
	plan_ids: string[];
};
export type WorkflowSummary = {
	id: string;
	name: string;
	runs: number;
	completed: number;
	active: number;
	configurations: WorkflowConfiguration[];
	groups: { id: string; name: string }[];
};
export type WorkflowStep = {
	key: string;
	position: number;
	title: string;
	task_type: 'QUESTION' | 'ESSAY' | 'SURVEY';
	started: number;
	completed: number;
	skipped: number;
	pending: number;
	average_elapsed_seconds: number | null;
	average_score: number | null;
	maximum_score: number | null;
	pending_grades: number;
	essay_topics: {
		title: string;
		question: string;
		submissions: number;
		average_words: number;
		minimum_words: number;
		maximum_words: number;
	}[];
	usage: { prompts: number; responses: number };
};
export type WorkflowDetail = WorkflowSummary & {
	configuration_id: string;
	progression_mode: string;
	chat_mode: string;
	steps: WorkflowStep[];
	conditions: { key: string; name: string; runs: number }[];
};
export const getResearchWorkflow = (
	token: string,
	id: string,
	filters: ResearchFilters = {},
	signal?: AbortSignal
): Promise<WorkflowDetail> =>
	request(token, `/workflows/${encodeURIComponent(id)}`, filters, signal);
export type WorkflowStepResults = {
	step: WorkflowStep;
	configuration_id: string;
	items: Array<{
		session_task_id: string;
		session_id: string;
		position: number;
		title: string;
		task_type: 'QUESTION' | 'ESSAY' | 'SURVEY';
		status: string;
		name: string;
		participant_id: string;
		is_demo: boolean;
		group_id: string;
		started_at: number | null;
		completed_at: number | null;
		finalized_at: number | null;
		question_submission?: {
			submission_id: string;
			status: string;
			grading_status: string;
			score: number | null;
			maximum_score: number;
			provisional_score: number | null;
		};
		essay?: {
			content: string;
			topic_title: string;
			topic_question: string;
			word_count: number | null;
			is_draft: boolean;
		};
		survey_submission?: {
			submission_id: string;
			status: string;
			answers: Array<{ prompt: string; value: string | number | string[] | null }>;
		};
	}>;
	total: number;
	page: number;
	limit: number;
	survey_distributions: Array<{
		position: number;
		prompt: string;
		question_type: string;
		answered: number;
		counts: Record<string, number>;
	}>;
};
export const getWorkflowStepResults = (
	token: string,
	id: string,
	step: string,
	filters: ResearchFilters = {},
	signal?: AbortSignal
): Promise<WorkflowStepResults> =>
	request(
		token,
		`/workflows/${encodeURIComponent(id)}/steps/${encodeURIComponent(step)}`,
		filters,
		signal
	);
