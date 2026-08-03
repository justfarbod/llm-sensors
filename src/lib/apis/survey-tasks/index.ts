import { WEBUI_API_BASE_URL } from '$lib/constants';

export type SurveyQuestionType =
	| 'SINGLE_CHOICE'
	| 'MULTIPLE_SELECT'
	| 'SCALE'
	| 'SHORT_TEXT'
	| 'LONG_TEXT';

export type SurveyChoice = { id?: string; text: string; position?: number };
export type SurveyQuestion = {
	id?: string;
	prompt: string;
	description: string;
	question_type: SurveyQuestionType;
	required: boolean;
	enabled: boolean;
	choices: SurveyChoice[];
	scale_low_label?: string | null;
	scale_high_label?: string | null;
};

export type SurveyTask = {
	id: string;
	family_id: string;
	version: number;
	latest_version: number;
	title: string;
	description: string;
	status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED';
	locked_at?: number | null;
	questions: SurveyQuestion[];
};

const request = async (token: string, path = '', init: RequestInit = {}) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/survey-tasks${path}`, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`,
			...(init.headers ?? {})
		}
	});
	const body = await response.json().catch(() => null);
	if (!response.ok) throw body?.detail ?? `Request failed with status ${response.status}`;
	return body;
};

export const getSurveyTasks = (token: string) => request(token);
export const createSurveyTask = (token: string, body: Partial<SurveyTask>) =>
	request(token, '', { method: 'POST', body: JSON.stringify(body) });
export const updateSurveyTask = (token: string, id: string, body: Partial<SurveyTask>) =>
	request(token, `/${id}`, { method: 'PUT', body: JSON.stringify(body) });
export const cloneSurveyTask = (token: string, id: string, mode: 'VERSION' | 'DUPLICATE') =>
	request(token, `/${id}/clone`, { method: 'POST', body: JSON.stringify({ mode }) });
export const publishSurveyTask = (token: string, id: string) =>
	request(token, `/${id}/publish`, { method: 'POST' });
export const archiveSurveyTask = (token: string, id: string) =>
	request(token, `/${id}`, { method: 'DELETE' });
