import type { LLMPromptBudget } from '$lib/utils/experimentPromptBudgets';
import { WEBUI_API_BASE_URL } from '$lib/constants';
import type { ExperimentCondition } from '$lib/apis/experiment-plans';

export type WorkflowIssue = { path: string; message: string };
export type WorkflowItem = {
	llm_prompt_budget?: LLMPromptBudget | null;
	key: string;
	task_type: 'ESSAY' | 'QUESTION' | 'SURVEY';
	title: string;
	enabled?: boolean;
	essay_topic_mode?: 'SPECIFIC' | 'RANDOM_ALL' | 'RANDOM_SELECTED';
	essay_topic_id?: string | null;
	essay_topic_ids?: string[];
	question_task_id?: string | null;
	survey_task_id?: string | null;
	survey_required?: boolean;
};
export type WorkflowDefinition = {
	progression_mode: 'STRICT_SEQUENTIAL' | 'FREE_NAVIGATION' | 'SEQUENTIAL_REVIEW';
	chat_mode: 'SHARED_EXPERIMENT' | 'FRESH_PER_TASK';
	consent_enabled: boolean;
	items: WorkflowItem[];
	conditions: Array<ExperimentCondition & { key?: string }>;
};
export type ExperimentWorkflow = {
	id: string;
	name: string;
	description: string;
	definition: WorkflowDefinition;
	revision: number;
	status: 'DRAFT' | 'READY';
	issues: WorkflowIssue[];
	created_at: number;
	updated_at: number;
};

const request = async (token: string, path = '', init: RequestInit = {}) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/experiment-workflows${path}`, {
		...init,
		headers: {
			Accept: 'application/json',
			...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
			authorization: `Bearer ${token}`,
			...(init.headers ?? {})
		}
	});
	if (!response.ok) {
		const body = await response.json().catch(() => null);
		throw body?.detail ?? `Request failed with status ${response.status}`;
	}
	return response;
};

export const getExperimentWorkflows = async (token: string) =>
	(await request(token)).json() as Promise<ExperimentWorkflow[]>;
export const createExperimentWorkflow = async (
	token: string,
	body: Pick<ExperimentWorkflow, 'name' | 'description'> & { definition?: WorkflowDefinition }
) =>
	(
		await request(token, '', {
			method: 'POST',
			body: JSON.stringify({ ...body, definition: body.definition ?? {} })
		})
	).json() as Promise<ExperimentWorkflow>;
export const updateExperimentWorkflow = async (token: string, workflow: ExperimentWorkflow) =>
	(
		await request(token, `/${workflow.id}`, {
			method: 'PUT',
			body: JSON.stringify({
				name: workflow.name,
				description: workflow.description,
				definition: workflow.definition,
				revision: workflow.revision
			})
		})
	).json() as Promise<ExperimentWorkflow>;
export const deleteExperimentWorkflow = (token: string, id: string, revision: number) =>
	request(token, `/${id}?revision=${revision}`, { method: 'DELETE' });
export const importExperimentWorkflow = async (token: string, file: File) => {
	const data = new FormData();
	data.append('file', file);
	return (
		await request(token, '/import', { method: 'POST', body: data })
	).json() as Promise<ExperimentWorkflow>;
};
export const exportExperimentWorkflow = async (token: string, id: string) => {
	const response = await request(token, `/${id}/export`);
	return {
		blob: await response.blob(),
		disposition: response.headers.get('content-disposition') ?? ''
	};
};
export const applyExperimentWorkflow = async (
	token: string,
	id: string,
	body: { group_id: string; workflow_revision: number; expected_current_plan_version: number }
) => (await request(token, `/${id}/apply`, { method: 'POST', body: JSON.stringify(body) })).json();
