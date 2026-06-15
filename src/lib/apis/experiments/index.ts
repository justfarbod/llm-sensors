import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ExperimentState =
	| 'NOT_APPLICABLE'
	| 'CONSENT_REQUIRED'
	| 'PRE_SURVEY_REQUIRED'
	| 'TOPIC_REQUIRED'
	| 'IN_PROGRESS'
	| 'POST_SURVEY_REQUIRED'
	| 'THANK_YOU_REQUIRED'
	| 'COMPLETED'
	| 'CONFIGURATION_ERROR';

export type ExperimentCurrent = {
	state: ExperimentState;
	session_id?: string | null;
	group_id?: string | null;
	topic?: { id: string; title: string; question: string } | null;
	agreement_text?: string | null;
	error?: string | null;
};

const request = async (token: string, path = '', options: RequestInit = {}) => {
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
	return body as ExperimentCurrent;
};

export const getCurrentExperiment = (token: string) => request(token);
export const submitExperimentConsent = (token: string) => request(token, '/consent', { method: 'POST' });
export const submitExperimentPreSurvey = (token: string, body: object) =>
	request(token, '/pre-survey', { method: 'POST', body: JSON.stringify(body) });
export const startExperiment = (token: string) => request(token, '/start', { method: 'POST' });
export const submitExperimentPostSurvey = (token: string, body: object) =>
	request(token, '/post-survey', { method: 'POST', body: JSON.stringify(body) });
export const completeExperiment = (token: string) => request(token, '/complete', { method: 'POST' });

