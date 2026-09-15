<script lang="ts">
	import { getContext } from 'svelte';
	import { getQuestionTask } from '$lib/apis/question-tasks';
	import {
		defaultPromptBudget,
		questionPromptBudget,
		type LLMPromptBudget
	} from '$lib/utils/experimentPromptBudgets';
	const i18n = getContext<any>('i18n');
	export let budget: LLMPromptBudget | null | undefined = undefined;
	export let taskType: string;
	export let questionTaskId: string | null | undefined = null;
	export let onChange: () => void = () => {};
	let questions: { id: string; title: string }[] = [];
	let loadedId: string | null | undefined;
	let loading = false;
	let error = '';
	$: if (!budget) budget = defaultPromptBudget();
	const load = async (id: string) => {
		loadedId = id;
		loading = true;
		error = '';
		try {
			const task = await getQuestionTask(localStorage.token, id);
			if (loadedId === id) questions = task.questions;
		} catch (e) {
			if (loadedId === id) error = String(e);
		} finally {
			if (loadedId === id) loading = false;
		}
	};
	$: if (taskType === 'QUESTION' && questionTaskId && loadedId !== questionTaskId)
		void load(questionTaskId);
	const changeMode = (mode: string) => {
		budget = mode === 'TASK' ? defaultPromptBudget() : questionPromptBudget(questions);
		onChange();
	};
</script>

<div class="mt-3 rounded-xl bg-gray-50 p-3 text-xs dark:bg-gray-900">
	<div class="mb-2 font-medium">{$i18n.t('LLM prompt limit')}</div>
	{#if taskType === 'QUESTION'}
		<label class="block mb-2">
			{$i18n.t('Budget applies to')}
			<select
				class="mt-1 block w-full rounded-lg border bg-transparent p-2"
				value={budget?.mode}
				disabled={loading || !!error}
				on:change={(e) => changeMode(e.currentTarget.value)}
			>
				<option value="TASK">{$i18n.t('Whole task')}</option>
				<option value="PER_QUESTION">{$i18n.t('Each question separately')}</option>
			</select>
		</label>
	{/if}
	{#if budget?.mode === 'TASK'}
		<label
			>{$i18n.t('Maximum prompts')}
			<input
				class="ml-2 w-24 rounded-lg border bg-transparent p-2"
				type="number"
				min="0"
				max="2147483647"
				step="1"
				required
				bind:value={budget.limit}
				on:input={onChange}
			/>
		</label>
	{:else if budget}
		{#each questions as question, index (question.id)}
			<label class="my-2 flex items-center justify-between gap-3">
				<span>{index + 1}. {question.title}</span>
				<input
					class="w-24 shrink-0 rounded-lg border bg-transparent p-2"
					type="number"
					min="0"
					max="2147483647"
					step="1"
					required
					bind:value={budget.question_limits[question.id]}
					on:input={onChange}
				/>
			</label>
		{/each}
	{/if}
	{#if error}<p role="alert" class="mt-2 text-red-500">{error}</p>{/if}
	<p class="mt-2 text-gray-500">
		{$i18n.t('Only successful responses count. Set 0 to disable LLM use.')}
	</p>
</div>
