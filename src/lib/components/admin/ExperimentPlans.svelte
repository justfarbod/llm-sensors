<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { getGroups } from '$lib/apis/groups';
	import { getEssayTopics } from '$lib/apis/essays';
	import { getQuestionTasks, type QuestionTask } from '$lib/apis/question-tasks';
	import { getSurveyTasks, type SurveyTask } from '$lib/apis/survey-tasks';
	import {
		getExperimentPlan,
		saveExperimentPlan,
		type ExperimentPlan,
		type PlanItem
	} from '$lib/apis/experiment-plans';

	type Group = {
		id: string;
		name: string;
		permissions?: { features?: { essay_sidebar?: boolean } };
		data?: { experiment_mode_enabled?: boolean };
	};
	type EssayTopic = { id: string; title: string; question: string };

	const i18n: Writable<i18nType> = getContext('i18n');
	let groups: Group[] = [];
	let topics: EssayTopic[] = [];
	let questionTasks: QuestionTask[] = [];
	let surveyTasks: SurveyTask[] = [];
	let selectedGroupId = '';
	let plan: ExperimentPlan = {
		progression_mode: 'STRICT_SEQUENTIAL',
		chat_mode: 'SHARED_EXPERIMENT',
		consent_enabled: true,
		items: []
	};
	let loading = true;
	let saving = false;
	const newerQuestionVersionExists = (task: QuestionTask) =>
		questionTasks.some(
			(candidate) => candidate.family_id === task.family_id && candidate.version > task.version
		);
	const newerSurveyVersionExists = (task: SurveyTask) =>
		surveyTasks.some(
			(candidate) => candidate.family_id === task.family_id && candidate.version > task.version
		);

	const loadPlan = async () => {
		if (!selectedGroupId) return;
		loading = true;
		try {
			plan = await getExperimentPlan(localStorage.token, selectedGroupId);
		} catch (error) {
			toast.error(String(error));
		} finally {
			loading = false;
		}
	};

	const addEssay = () => {
		plan.items = [
			...plan.items,
			{
				task_type: 'ESSAY',
				title: $i18n.t('Essay Task'),
				essay_topic_mode: 'SPECIFIC',
				essay_topic_id: topics[0]?.id ?? null,
				essay_topic_ids: []
			}
		];
	};

	const addQuestion = () => {
		const task = questionTasks[0];
		plan.items = [
			...plan.items,
			{
				task_type: 'QUESTION',
				title: task?.title ?? $i18n.t('Question Task'),
				question_task_id: task?.id ?? null
			}
		];
	};

	const addSurvey = () => {
		const task = surveyTasks[0];
		plan.items = [
			...plan.items,
			{
				task_type: 'SURVEY',
				title: task?.title ?? $i18n.t('Survey Task'),
				survey_task_id: task?.id ?? null,
				survey_required: true,
				enabled: true
			}
		];
	};

	const move = (index: number, offset: number) => {
		const target = index + offset;
		if (target < 0 || target >= plan.items.length) return;
		[plan.items[index], plan.items[target]] = [plan.items[target], plan.items[index]];
		plan.items = [...plan.items];
	};

	const setQuestionTask = (item: PlanItem, taskId: string) => {
		item.question_task_id = taskId;
		const task = questionTasks.find((candidate) => candidate.id === taskId);
		if (task) item.title = task.title;
		plan.items = [...plan.items];
	};

	const setSurveyTask = (item: PlanItem, taskId: string) => {
		item.survey_task_id = taskId;
		const task = surveyTasks.find((candidate) => candidate.id === taskId);
		if (task) item.title = task.title;
		plan.items = [...plan.items];
	};

	const invalidSurveyPlacement = () => {
		if (plan.progression_mode === 'STRICT_SEQUENTIAL') return false;
		const enabled = plan.items.filter((item) => item.enabled !== false);
		const positions = enabled
			.map((item, index) => (item.task_type === 'SURVEY' ? -1 : index))
			.filter((index) => index >= 0);
		if (!positions.length) return false;
		return enabled
			.slice(Math.min(...positions), Math.max(...positions) + 1)
			.some((item) => item.task_type === 'SURVEY');
	};

	const togglePoolTopic = (item: PlanItem, topicId: string, checked: boolean) => {
		const current = item.essay_topic_ids ?? [];
		item.essay_topic_ids = checked
			? Array.from(new Set([...current, topicId]))
			: current.filter((id) => id !== topicId);
		plan.items = [...plan.items];
	};

	const save = async () => {
		if (!selectedGroupId || !plan.items.length || saving) return;
		if (invalidSurveyPlacement()) {
			toast.error(
				$i18n.t('Surveys may appear between tasks only in strict sequential experiments.')
			);
			return;
		}
		saving = true;
		try {
			plan = await saveExperimentPlan(localStorage.token, selectedGroupId, plan);
			toast.success($i18n.t('Experiment plan published'));
		} catch (error) {
			toast.error(String(error));
		} finally {
			saving = false;
		}
	};

	onMount(async () => {
		try {
			const [groupResponse, topicResponse, taskResponse, surveyResponse] = await Promise.all([
				getGroups(localStorage.token),
				getEssayTopics(localStorage.token),
				getQuestionTasks(localStorage.token),
				getSurveyTasks(localStorage.token)
			]);
			groups = Array.isArray(groupResponse) ? groupResponse : [];
			topics = Array.isArray(topicResponse) ? topicResponse : [];
			questionTasks = (Array.isArray(taskResponse) ? taskResponse : []).filter(
				(task) => task.status === 'PUBLISHED'
			);
			surveyTasks = (Array.isArray(surveyResponse) ? surveyResponse : []).filter(
				(task) => task.status === 'PUBLISHED'
			);
			selectedGroupId = groups[0]?.id ?? '';
			if (selectedGroupId) await loadPlan();
		} catch (error) {
			toast.error(String(error));
		} finally {
			loading = false;
		}
	});
</script>

<div class="space-y-4">
	<div>
		<div class="text-xl font-medium">{$i18n.t('Experiment Plans')}</div>
		<p class="mt-1 text-sm text-gray-500">
			{$i18n.t('Build the ordered Essay and Question Tasks assigned to each experiment group.')}
		</p>
	</div>

	<div
		class="grid gap-3 rounded-2xl border border-gray-100 p-4 md:grid-cols-4 dark:border-gray-850"
	>
		<label class="text-xs font-medium text-gray-500">
			{$i18n.t('Group')}
			<select class="plan-input mt-1" bind:value={selectedGroupId} on:change={loadPlan}>
				{#each groups as group}
					<option value={group.id}>{group.name}</option>
				{/each}
			</select>
		</label>
		<label
			class="flex items-center gap-2 self-end rounded-xl bg-gray-50 px-3 py-2 text-xs font-medium dark:bg-gray-900"
		>
			<input type="checkbox" bind:checked={plan.consent_enabled} />
			{$i18n.t('Require consent first')}
		</label>
		<label class="text-xs font-medium text-gray-500">
			{$i18n.t('Progression')}
			<select class="plan-input mt-1" bind:value={plan.progression_mode}>
				<option value="STRICT_SEQUENTIAL">{$i18n.t('Strict sequential')}</option>
				<option value="FREE_NAVIGATION">{$i18n.t('Free navigation')}</option>
				<option value="SEQUENTIAL_REVIEW">{$i18n.t('Sequential unlock with review')}</option>
			</select>
		</label>
		<label class="text-xs font-medium text-gray-500">
			{$i18n.t('Chat continuity')}
			<select class="plan-input mt-1" bind:value={plan.chat_mode}>
				<option value="SHARED_EXPERIMENT">{$i18n.t('Shared across experiment')}</option>
				<option value="FRESH_PER_TASK">{$i18n.t('Fresh chat for each task')}</option>
			</select>
		</label>
	</div>

	{#if !selectedGroupId}
		<div class="py-16 text-center text-sm text-gray-500">
			{$i18n.t('Create a group before configuring an experiment plan.')}
		</div>
	{:else if loading}
		<div class="py-16 text-center text-sm text-gray-500">{$i18n.t('Loading...')}</div>
	{:else}
		<div class="space-y-3">
			{#each plan.items as item, index (item.id ?? index)}
				<div class="rounded-2xl border border-gray-100 p-4 dark:border-gray-850">
					<div class="flex flex-wrap items-center gap-2">
						<span
							class="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-semibold dark:bg-gray-800"
						>
							{index + 1}. {$i18n.t(
								item.task_type === 'ESSAY'
									? 'Essay Task'
									: item.task_type === 'QUESTION'
										? 'Question Task'
										: 'Survey Task'
							)}
						</span>
						<input
							class="plan-input !w-auto min-w-52 flex-1"
							bind:value={item.title}
							placeholder={$i18n.t('Participant-facing task title')}
						/>
						<button
							class="plan-button"
							disabled={index === 0}
							on:click={() => move(index, -1)}
							aria-label={$i18n.t('Move up')}>↑</button
						>
						<button
							class="plan-button"
							disabled={index === plan.items.length - 1}
							on:click={() => move(index, 1)}
							aria-label={$i18n.t('Move down')}>↓</button
						>
						<button
							class="plan-button text-red-600"
							on:click={() =>
								(plan.items = plan.items.filter((_, itemIndex) => itemIndex !== index))}
							>{$i18n.t('Delete')}</button
						>
					</div>

					{#if item.task_type === 'QUESTION'}
						<label class="mt-3 block text-xs font-medium text-gray-500">
							{$i18n.t('Published Question Task')}
							<select
								class="plan-input mt-1"
								value={item.question_task_id ?? ''}
								on:change={(event) => setQuestionTask(item, event.currentTarget.value)}
							>
								<option value="" disabled>{$i18n.t('Select a Question Task')}</option>
								{#each questionTasks as task}<option value={task.id}
										>{task.title} · v{task.version}{newerQuestionVersionExists(task)
											? ` · ${$i18n.t('newer published version available')}`
											: ''}</option
									>{/each}
							</select>
						</label>
					{:else if item.task_type === 'SURVEY'}
						<div class="mt-3 grid gap-3 md:grid-cols-3">
							<label class="text-xs font-medium text-gray-500 md:col-span-2">
								{$i18n.t('Published Survey Task')}
								<select
									class="plan-input mt-1"
									value={item.survey_task_id ?? ''}
									on:change={(event) => setSurveyTask(item, event.currentTarget.value)}
								>
									<option value="" disabled>{$i18n.t('Select a Survey Task')}</option>
									{#each surveyTasks as task}<option value={task.id}
											>{task.title} · v{task.version}{newerSurveyVersionExists(task)
												? ` · ${$i18n.t('newer published version available')}`
												: ''}</option
										>{/each}
								</select>
							</label>
							<div class="flex flex-col justify-end gap-2 text-xs">
								<label
									><input type="checkbox" bind:checked={item.enabled} /> {$i18n.t('Enabled')}</label
								>
								<label
									><input type="checkbox" bind:checked={item.survey_required} />
									{$i18n.t('Required stage')}</label
								>
							</div>
						</div>
					{:else}
						<div class="mt-3 grid gap-3 md:grid-cols-2">
							<label class="text-xs font-medium text-gray-500">
								{$i18n.t('Topic selection')}
								<select class="plan-input mt-1" bind:value={item.essay_topic_mode}>
									<option value="SPECIFIC">{$i18n.t('Specific topic')}</option>
									<option value="RANDOM_ALL">{$i18n.t('Random from all topics')}</option>
									<option value="RANDOM_SELECTED">{$i18n.t('Random from selected pool')}</option>
								</select>
							</label>
							{#if item.essay_topic_mode === 'SPECIFIC'}
								<label class="text-xs font-medium text-gray-500">
									{$i18n.t('Essay topic')}
									<select class="plan-input mt-1" bind:value={item.essay_topic_id}>
										<option value="" disabled>{$i18n.t('Select a topic')}</option>
										{#each topics as topic}<option value={topic.id}>{topic.title}</option>{/each}
									</select>
								</label>
							{/if}
						</div>
						{#if item.essay_topic_mode === 'RANDOM_SELECTED'}
							<div class="mt-3 rounded-xl bg-gray-50 p-3 dark:bg-gray-900">
								<div class="mb-2 text-xs font-medium">
									{$i18n.t('Topic pool — choose at least two')}
								</div>
								<div class="grid gap-2 sm:grid-cols-2">
									{#each topics as topic}
										<label class="flex items-center gap-2 text-xs"
											><input
												type="checkbox"
												checked={(item.essay_topic_ids ?? []).includes(topic.id)}
												on:change={(event) =>
													togglePoolTopic(item, topic.id, event.currentTarget.checked)}
											/>
											{topic.title}</label
										>
									{/each}
								</div>
							</div>
						{/if}
					{/if}
				</div>
			{:else}
				<div
					class="rounded-2xl border border-dashed border-gray-200 py-14 text-center text-sm text-gray-500 dark:border-gray-800"
				>
					{$i18n.t('Add at least one task to publish this experiment plan.')}
				</div>
			{/each}
		</div>

		<div class="flex flex-wrap items-center justify-between gap-2">
			<div class="flex gap-2">
				<button class="plan-button" disabled={!topics.length} on:click={addEssay}
					>+ {$i18n.t('Essay Task')}</button
				>
				<button class="plan-button" disabled={!questionTasks.length} on:click={addQuestion}
					>+ {$i18n.t('Question Task')}</button
				>
				<button class="plan-button" disabled={!surveyTasks.length} on:click={addSurvey}
					>+ {$i18n.t('Survey Task')}</button
				>
			</div>
			<div class="flex items-center gap-3">
				{#if plan.version}<span class="text-xs text-gray-500"
						>{$i18n.t('Version')}
						{plan.version}{plan.locked_at ? ` · ${$i18n.t('locked')}` : ''}</span
					>{/if}
				<button
					class="rounded-full bg-black px-5 py-2 text-xs font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
					disabled={saving || !plan.items.length}
					on:click={save}>{saving ? $i18n.t('Saving...') : $i18n.t('Publish plan')}</button
				>
			</div>
		</div>

		{#if groups.find((group) => group.id === selectedGroupId)?.permissions?.features?.essay_sidebar !== true}
			<p
				class="rounded-xl bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
			>
				{$i18n.t('Enable the Experiment Task Sidebar permission for this group before publishing.')}
			</p>
		{/if}
		{#if invalidSurveyPlacement()}<p
				class="rounded-xl bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/20 dark:text-red-200"
			>
				{$i18n.t('Surveys may appear between tasks only in strict sequential experiments.')}
			</p>{/if}
	{/if}
</div>

<style>
	:global(.plan-input) {
		width: 100%;
		min-width: 0;
		border-radius: 0.65rem;
		background: rgb(249 250 251);
		padding: 0.55rem 0.7rem;
		font-size: 0.8rem;
		outline: none;
	}
	:global(.dark .plan-input) {
		background: rgb(17 24 39);
	}
	:global(.plan-button) {
		border-radius: 0.6rem;
		background: rgb(243 244 246);
		padding: 0.45rem 0.7rem;
		font-size: 0.75rem;
		font-weight: 500;
	}
	:global(.dark .plan-button) {
		background: rgb(31 41 55);
	}
</style>
