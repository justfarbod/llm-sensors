import { WEBUI_API_BASE_URL } from '$lib/constants';

export type QuestionType = 'SINGLE_CHOICE' | 'MULTIPLE_SELECT' | 'FILL_BLANK' | 'FREE_TEXT';
export type GradingMode = 'AUTOMATIC' | 'MANUAL' | 'LLM_ASSISTED';

export type QuestionChoice = { id?: string; text: string; is_correct: boolean; position?: number };
export type QuestionBlank = {
	id?: string;
	key: string;
	accepted_answers: string[];
	position?: number;
};
export type QuestionDefinition = {
	id?: string;
	title: string;
	description: string;
	question_type: QuestionType;
	max_score: number;
	grading_mode: GradingMode;
	image_file_id?: string | null;
	case_sensitive?: boolean;
	choices: QuestionChoice[];
	blanks: QuestionBlank[];
	expected_answer?: string | null;
	strictness?: 'LENIENT' | 'BALANCED' | 'STRICT' | null;
};

export type QuestionTask = {
	id: string;
	family_id: string;
	version: number;
	latest_version: number;
	title: string;
	description: string;
	status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED';
	locked_at?: number | null;
	questions: QuestionDefinition[];
};

export type QuestionTaskForm = Pick<QuestionTask, 'title' | 'description' | 'questions'>;

const request = async (token: string, path = '', init: RequestInit = {}) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/question-tasks${path}`, {
		...init,
		headers: {
			Accept: 'application/json',
			...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
			authorization: `Bearer ${token}`,
			...(init.headers ?? {})
		}
	});
	const body = await response.json().catch(() => null);
	if (!response.ok) throw body?.detail ?? `Request failed with status ${response.status}`;
	return body;
};

export const getQuestionTask = (token: string, id: string) => request(token, `/${id}`);
export const getQuestionTasks = (token: string) => request(token);
export const createQuestionTask = (token: string, body: QuestionTaskForm) =>
	request(token, '', { method: 'POST', body: JSON.stringify(body) });
export const updateQuestionTask = (token: string, id: string, body: QuestionTaskForm) =>
	request(token, `/${id}`, { method: 'PUT', body: JSON.stringify(body) });
export const publishQuestionTask = (token: string, id: string) =>
	request(token, `/${id}/publish`, { method: 'POST' });
export const cloneQuestionTask = (token: string, id: string, mode: 'VERSION' | 'DUPLICATE') =>
	request(token, `/${id}/clone`, { method: 'POST', body: JSON.stringify({ mode }) });
export const archiveQuestionTask = (token: string, id: string) =>
	request(token, `/${id}`, { method: 'DELETE' });
export const uploadQuestionImage = (token: string, file: File) => {
	const data = new FormData();
	data.append('file', file);
	return request(token, '/images', { method: 'POST', body: data });
};
export const questionImageUrl = (id: string) =>
	`${WEBUI_API_BASE_URL}/question-tasks/images/${id}/content`;
