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

export const exportResearchData = async (
	token: string,
	section: 'participants' | 'essays' | 'surveys',
	ids: string[],
	format: 'csv' | 'json',
	anonymized: boolean
) => {
	const response = await fetch(`${baseUrl}/export/${section}`, {
		method: 'POST',
		headers: {
			Accept: format === 'csv' ? 'text/csv' : 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ ids, format, anonymized })
	});
	if (!response.ok) {
		const body = await response.json().catch(() => null);
		throw body?.detail ?? `Export failed with status ${response.status}`;
	}
	return response.blob();
};
