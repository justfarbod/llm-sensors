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

export type ActivationRule = {
	mode:
		| 'EVERY_REQUEST'
		| 'FIRST_REQUEST'
		| 'AFTER_PROMPT_COUNT'
		| 'EVERY_N_PROMPTS'
		| 'PROMPT_RANGE';
	count?: number | null;
	range_start?: number | null;
	range_end?: number | null;
	probability: number;
	scope: 'ALL_TASKS' | 'SELECTED_TASKS';
	plan_item_ids: string[];
};

export type ExperimentCondition = {
	id?: string;
	name: string;
	allocation_percent: number;
	enabled: boolean;
	is_control: boolean;
	prompt_injection: {
		enabled: boolean;
		instruction: string;
		position: 'SYSTEM' | 'BEFORE_PARTICIPANT' | 'AFTER_PARTICIPANT';
		activation: ActivationRule;
	};
	warning_modal: {
		enabled: boolean;
		title: string;
		message: string;
		confirmation_text: string;
		must_acknowledge: boolean;
		cadence:
			| 'BEGINNING'
			| 'EVERY_N_PROMPTS'
			| 'PROMPT_LIST'
			| 'ONCE_AFTER_PROMPT'
			| 'EVERY_N_ACTIVE_MINUTES'
			| 'AFTER_ACTIVE_MINUTES'
			| 'ONCE_AFTER_ACTIVE_DELAY';
		cadence_value?: number | null;
		prompt_numbers: number[];
	};
	response_timing: {
		mode: 'NORMAL' | 'DELAYED' | 'SLOW' | 'FAST';
		delay_seconds?: number | null;
		show_loading: boolean;
		reveal_style: 'FULL' | 'QUICK_STREAM';
		target_duration_seconds?: number | null;
		stream_unit: 'CHARACTER' | 'WORD' | 'CHUNK';
		minimum_chunk_size: number;
		maximum_chunk_size: number;
		punctuation_pauses: boolean;
		rate_value?: number | null;
		rate_unit: 'CHARACTERS_PER_SECOND' | 'WORDS_PER_SECOND' | 'TARGET_DURATION';
	};
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
	conditions: ExperimentCondition[];
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
