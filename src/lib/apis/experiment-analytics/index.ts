import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ResearchFilters = {
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
