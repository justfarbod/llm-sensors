import { WEBUI_API_BASE_URL } from '$lib/constants';

export type PlanItem = {
	id?: string;
	task_type: 'ESSAY' | 'QUESTION' | 'SURVEY';
	title: string;
	question_task_id?: string | null;
	essay_topic_mode?: 'SPECIFIC' | 'RANDOM_ALL' | 'RANDOM_SELECTED' | null;
	essay_topic_id?: string | null;
	essay_topic_ids?: string[];
	survey_task_id?: string | null;
	survey_required?: boolean;
	enabled?: boolean;
};

export type ExperimentPlan = {
	id?: string;
	group_id?: string;
	version?: number;
	status?: string;
	progression_mode: 'STRICT_SEQUENTIAL' | 'FREE_NAVIGATION' | 'SEQUENTIAL_REVIEW';
	chat_mode: 'SHARED_EXPERIMENT' | 'FRESH_PER_TASK';
	consent_enabled: boolean;
	survey_variant?: string;
	locked_at?: number | null;
	items: PlanItem[];
};

const request = async (token: string, groupId: string, init: RequestInit = {}) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/experiment-plans/groups/${groupId}`, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});
	const body = await response.json().catch(() => null);
	if (!response.ok) throw body?.detail ?? `Request failed with status ${response.status}`;
	return body as ExperimentPlan;
};

export const getExperimentPlan = (token: string, groupId: string) => request(token, groupId);
export const saveExperimentPlan = (token: string, groupId: string, plan: ExperimentPlan) =>
	request(token, groupId, { method: 'PUT', body: JSON.stringify(plan) });
