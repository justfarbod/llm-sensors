<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { getGroups } from '$lib/apis/groups';
	import { getEssayTopics } from '$lib/apis/essays';
	import { getQuestionTasks, type QuestionTask } from '$lib/apis/question-tasks';
	import { getSurveyTasks, type SurveyTask } from '$lib/apis/survey-tasks';
	import { getExperimentPlan } from '$lib/apis/experiment-plans';
	import {
		applyExperimentWorkflow,
		createExperimentWorkflow,
		deleteExperimentWorkflow,
		exportExperimentWorkflow,
		getExperimentWorkflows,
		importExperimentWorkflow,
		updateExperimentWorkflow,
		type ExperimentWorkflow,
		type WorkflowItem
	} from '$lib/apis/experiment-workflows';
	import ExperimentPerturbations from './ExperimentPerturbations.svelte';
	import './experiment-admin.css';

	type Group = {
		id: string;
		name: string;
		permissions?: { features?: { essay_sidebar?: boolean } };
	};
	type EssayTopic = { id: string; title: string; question: string };

	export let onResourcesImported: () => void | Promise<void> = () => {};

	const i18n: Writable<i18nType> = getContext('i18n');
	let workflows: ExperimentWorkflow[] = [];
	let groups: Group[] = [];
	let topics: EssayTopic[] = [];
	let questionTasks: QuestionTask[] = [];
	let surveyTasks: SurveyTask[] = [];
	let editing: ExperimentWorkflow | null = null;
	let loading = true;
	let saving = false;
	let dirty = false;
	let applying = false;
	let showApply = false;
	let selectedGroupId = '';
	let reviewPlanVersion = 0;
	let importInput: HTMLInputElement;

	$: conditionItems = (editing?.definition.items ?? []).map((item) => ({
		id: item.key,
		task_type: item.task_type,
		title: item.title
	}));

	const uuid = () => crypto.randomUUID();
	const changed = () => (dirty = true);
	const formatUpdatedAt = (value: number) =>
		new Date(value > 1_000_000_000_000_000 ? value / 1_000_000 : value * 1000).toLocaleString();
	const newerQuestionVersionExists = (task: QuestionTask) =>
		questionTasks.some(
			(candidate) => candidate.family_id === task.family_id && candidate.version > task.version
		);
	const newerSurveyVersionExists = (task: SurveyTask) =>
		surveyTasks.some(
			(candidate) => candidate.family_id === task.family_id && candidate.version > task.version
		);

	const load = async () => {
		loading = true;
		try {
			const [workflowResponse, groupResponse, topicResponse, questionResponse, surveyResponse] =
				await Promise.all([
					getExperimentWorkflows(localStorage.token),
					getGroups(localStorage.token),
					getEssayTopics(localStorage.token),
					getQuestionTasks(localStorage.token),
					getSurveyTasks(localStorage.token)
				]);
			workflows = Array.isArray(workflowResponse) ? workflowResponse : [];
			groups = Array.isArray(groupResponse) ? groupResponse : [];
			topics = Array.isArray(topicResponse) ? topicResponse : [];
			questionTasks = (Array.isArray(questionResponse) ? questionResponse : []).filter(
				(task) => task.status === 'PUBLISHED'
			);
			surveyTasks = (Array.isArray(surveyResponse) ? surveyResponse : []).filter(
				(task) => task.status === 'PUBLISHED'
			);
		} catch (error) {
			toast.error(String(error));
		} finally {
			loading = false;
		}
	};

	const open = (workflow: ExperimentWorkflow) => {
		if (dirty && !confirm($i18n.t('Discard unsaved workflow changes?'))) return;
		editing = structuredClone(workflow);
		dirty = false;
	};

	const close = () => {
		if (dirty && !confirm($i18n.t('Discard unsaved workflow changes?'))) return;
		editing = null;
		dirty = false;
	};

	const create = async () => {
		try {
			const workflow = await createExperimentWorkflow(localStorage.token, {
				name: $i18n.t('Untitled workflow'),
				description: ''
			});
			workflows = [workflow, ...workflows];
			open(workflow);
		} catch (error) {
			toast.error(String(error));
		}
	};

	const save = async () => {
		if (!editing || saving) return;
		saving = true;
		try {
			editing = await updateExperimentWorkflow(localStorage.token, editing);
			workflows = workflows.map((item) => (item.id === editing?.id ? editing : item));
			dirty = false;
			toast.success($i18n.t('Workflow saved'));
		} catch (error) {
			toast.error(typeof error === 'string' ? error : JSON.stringify(error));
		} finally {
			saving = false;
		}
	};

	const remove = async (workflow: ExperimentWorkflow) => {
		if (!confirm($i18n.t('Delete this workflow? Applied plans will not be affected.'))) return;
		try {
			await deleteExperimentWorkflow(localStorage.token, workflow.id, workflow.revision);
			workflows = workflows.filter((item) => item.id !== workflow.id);
			if (editing?.id === workflow.id) editing = null;
		} catch (error) {
			toast.error(String(error));
		}
	};

	const exportOne = async (workflow: ExperimentWorkflow) => {
		try {
			const { blob } = await exportExperimentWorkflow(localStorage.token, workflow.id);
			const name = workflow.name.replace(/[^A-Za-z0-9._-]+/g, '-') || 'experiment-workflow';
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = `${name}.owui-workflow.zip`;
			link.click();
			URL.revokeObjectURL(url);
		} catch (error) {
			toast.error(String(error));
		}
	};

	const importOne = async (file?: File) => {
		if (!file) return;
		try {
			const workflow = await importExperimentWorkflow(localStorage.token, file);
			await load();
			await onResourcesImported();
			open(workflows.find((candidate) => candidate.id === workflow.id) ?? workflow);
			toast.success($i18n.t('Workflow and its resources were imported'));
		} catch (error) {
			toast.error(typeof error === 'string' ? error : JSON.stringify(error));
		} finally {
			importInput.value = '';
		}
	};

	const addItem = (taskType: WorkflowItem['task_type']) => {
		if (!editing) return;
		const item: WorkflowItem = {
			key: uuid(),
			task_type: taskType,
			title: $i18n.t(
				taskType === 'ESSAY'
					? 'Essay Task'
					: taskType === 'QUESTION'
						? 'Question Task'
						: 'Survey Task'
			),
			enabled: true
		};
		if (taskType === 'ESSAY') {
			item.essay_topic_mode = 'SPECIFIC';
			item.essay_topic_id = topics[0]?.id ?? null;
			item.essay_topic_ids = [];
		} else if (taskType === 'QUESTION') {
			item.question_task_id = questionTasks[0]?.id ?? null;
			if (questionTasks[0]) item.title = questionTasks[0].title;
		} else {
			item.survey_task_id = surveyTasks[0]?.id ?? null;
			item.survey_required = true;
			if (surveyTasks[0]) item.title = surveyTasks[0].title;
		}
		editing.definition.items = [...editing.definition.items, item];
		changed();
	};

	const move = (index: number, offset: number) => {
		if (!editing) return;
		const target = index + offset;
		if (target < 0 || target >= editing.definition.items.length) return;
		[editing.definition.items[index], editing.definition.items[target]] = [
			editing.definition.items[target],
			editing.definition.items[index]
		];
		editing.definition.items = [...editing.definition.items];
		changed();
	};

	const deleteItem = (index: number) => {
		if (!editing) return;
		editing.definition.items = editing.definition.items.filter(
			(_, itemIndex) => itemIndex !== index
		);
		changed();
	};

	const setQuestionTask = (item: WorkflowItem, taskId: string) => {
		item.question_task_id = taskId;
		const task = questionTasks.find((candidate) => candidate.id === taskId);
		if (task) item.title = task.title;
		if (editing) editing.definition.items = [...editing.definition.items];
		changed();
	};

	const setSurveyTask = (item: WorkflowItem, taskId: string) => {
		item.survey_task_id = taskId;
		const task = surveyTasks.find((candidate) => candidate.id === taskId);
		if (task) item.title = task.title;
		if (editing) editing.definition.items = [...editing.definition.items];
		changed();
	};

	const togglePoolTopic = (item: WorkflowItem, topicId: string, checked: boolean) => {
		const current = item.essay_topic_ids ?? [];
		item.essay_topic_ids = checked
			? Array.from(new Set([...current, topicId]))
			: current.filter((id) => id !== topicId);
		if (editing) editing.definition.items = [...editing.definition.items];
		changed();
	};

	const refreshApplyReview = async () => {
		if (!selectedGroupId) {
			reviewPlanVersion = 0;
			return;
		}
		try {
			const current = await getExperimentPlan(localStorage.token, selectedGroupId);
			reviewPlanVersion = current.version ?? 0;
		} catch (error) {
			toast.error(String(error));
		}
	};

	const openApplyReview = async () => {
		selectedGroupId = groups.find((group) => group.permissions?.features?.essay_sidebar)?.id ?? '';
		await refreshApplyReview();
		showApply = true;
	};

	const apply = async () => {
		if (!editing || !selectedGroupId || applying) return;
		applying = true;
		try {
			await applyExperimentWorkflow(localStorage.token, editing.id, {
				group_id: selectedGroupId,
				workflow_revision: editing.revision,
				expected_current_plan_version: reviewPlanVersion
			});
			showApply = false;
			toast.success($i18n.t('Workflow published to group'));
		} catch (error) {
			toast.error(typeof error === 'string' ? error : JSON.stringify(error));
		} finally {
			applying = false;
		}
	};

	onMount(() => {
		void load();
		const warnBeforeUnload = (event: BeforeUnloadEvent) => {
			if (!dirty) return;
			event.preventDefault();
			event.returnValue = '';
		};
		window.addEventListener('beforeunload', warnBeforeUnload);
		return () => window.removeEventListener('beforeunload', warnBeforeUnload);
	});
</script>

<input
	class="hidden"
	type="file"
	accept=".zip,application/zip"
	bind:this={importInput}
	on:change={(event) => importOne(event.currentTarget.files?.[0])}
/>

{#if !editing}
	<div class="space-y-4">
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div>
				<div class="text-xl font-medium">{$i18n.t('Experiment Workflow Library')}</div>
				<p class="mt-1 text-sm text-gray-500">
					{$i18n.t('Combine published resources, experiment settings, and LLM conditions.')}
				</p>
			</div>
			<div class="flex gap-2">
				<button class="wf-button" on:click={() => importInput.click()}>{$i18n.t('Import')}</button>
				<button class="wf-primary" on:click={create}>+ {$i18n.t('New workflow')}</button>
			</div>
		</div>

		{#if loading}
			<div class="py-16 text-center text-sm text-gray-500">{$i18n.t('Loading...')}</div>
		{:else}
			<div class="grid gap-3 md:grid-cols-2">
				{#each workflows as workflow (workflow.id)}
					<article class="rounded-2xl border border-gray-100 p-4 dark:border-gray-850">
						<button class="w-full text-left" on:click={() => open(workflow)}>
							<div class="flex justify-between gap-2">
								<b class="truncate">{workflow.name}</b>
								<span
									class="rounded-full px-2 py-1 text-[11px] {workflow.status === 'READY'
										? 'bg-green-100 text-green-700'
										: 'bg-amber-100 text-amber-700'}">{$i18n.t(workflow.status)}</span
								>
							</div>
							<p class="mt-1 line-clamp-2 text-xs text-gray-500">
								{workflow.description || $i18n.t('No description')}
							</p>
							<div class="mt-2 text-xs text-gray-500">
								{workflow.definition.items.length}
								{$i18n.t('stages')} ·
								{workflow.definition.items.filter((item) => item.task_type === 'ESSAY').length}
								{$i18n.t('essays')} ·
								{workflow.definition.items.filter((item) => item.task_type === 'QUESTION').length}
								{$i18n.t('question tasks')} ·
								{workflow.definition.items.filter((item) => item.task_type === 'SURVEY').length}
								{$i18n.t('surveys')}
								<br />{$i18n.t('Updated')}
								{formatUpdatedAt(workflow.updated_at)}
							</div>
						</button>
						<div class="mt-3 flex gap-2">
							<button class="wf-button" on:click={() => open(workflow)}>{$i18n.t('Edit')}</button>
							<button class="wf-button" on:click={() => exportOne(workflow)}
								>{$i18n.t('Export')}</button
							>
							<button class="wf-button text-red-600" on:click={() => remove(workflow)}
								>{$i18n.t('Delete')}</button
							>
						</div>
					</article>
				{:else}
					<div class="col-span-full py-16 text-center text-sm text-gray-500">
						{$i18n.t('No workflows yet.')}
					</div>
				{/each}
			</div>
		{/if}
	</div>
{:else}
	<div class="space-y-4">
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div class="min-w-0 flex-1">
				<button class="wf-button mb-2" on:click={close}>← {$i18n.t('Workflow Library')}</button>
				<div class="grid gap-3">
					<label class="experiment-label">
						{$i18n.t('Workflow name')}
						<input class="experiment-title-input" bind:value={editing.name} on:input={changed} />
					</label>
					<label class="experiment-label">
						{$i18n.t('Description')}
						<textarea
							class="experiment-input min-h-16"
							bind:value={editing.description}
							on:input={changed}
						></textarea>
					</label>
				</div>
			</div>
			<div class="flex items-center gap-2">
				<span
					class="rounded-full px-3 py-1 text-xs {editing.status === 'READY'
						? 'bg-green-100 text-green-700'
						: 'bg-amber-100 text-amber-700'}">{$i18n.t(editing.status)}</span
				>
				<button class="wf-button" on:click={() => editing && exportOne(editing)}
					>{$i18n.t('Export')}</button
				>
				<button
					class="wf-button"
					disabled={editing.status !== 'READY' || dirty}
					on:click={openApplyReview}>{$i18n.t('Apply')}</button
				>
				<button class="wf-primary" disabled={saving || !editing.name.trim()} on:click={save}
					>{saving ? $i18n.t('Saving...') : $i18n.t('Save')}</button
				>
			</div>
		</div>

		{#if editing.issues.length}
			<div
				class="rounded-2xl bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
			>
				<b>{$i18n.t('Complete these fields before applying:')}</b>
				<ul class="mt-1 list-disc pl-5">
					{#each editing.issues.slice(0, 12) as issue}
						<li><code>{issue.path}</code>: {issue.message}</li>
					{/each}
				</ul>
			</div>
		{/if}

		<div
			class="grid gap-3 rounded-2xl border border-gray-100 p-4 md:grid-cols-3 dark:border-gray-850"
		>
			<label class="experiment-label">
				{$i18n.t('Progression')}
				<select
					class="experiment-input mt-1"
					bind:value={editing.definition.progression_mode}
					on:change={changed}
				>
					<option value="STRICT_SEQUENTIAL">{$i18n.t('Strict sequential')}</option>
					<option value="FREE_NAVIGATION">{$i18n.t('Free navigation')}</option>
					<option value="SEQUENTIAL_REVIEW">{$i18n.t('Sequential unlock with review')}</option>
				</select>
			</label>
			<label class="experiment-label">
				{$i18n.t('Chat continuity')}
				<select
					class="experiment-input mt-1"
					bind:value={editing.definition.chat_mode}
					on:change={changed}
				>
					<option value="SHARED_EXPERIMENT">{$i18n.t('Shared across experiment')}</option>
					<option value="FRESH_PER_TASK">{$i18n.t('Fresh chat for each task')}</option>
				</select>
			</label>
			<label
				class="flex items-center gap-2 self-end rounded-xl bg-gray-50 px-3 py-2 text-xs font-medium dark:bg-gray-900"
			>
				<input
					type="checkbox"
					bind:checked={editing.definition.consent_enabled}
					on:change={changed}
				/>
				{$i18n.t('Require consent first')}
			</label>
		</div>

		<div class="space-y-3">
			<div class="flex items-center justify-between">
				<div>
					<h3 class="font-medium">{$i18n.t('Ordered tasks')}</h3>
					<p class="text-xs text-gray-500">
						{$i18n.t(
							'Select resources created in the Essay Topics, Question Tasks, and Surveys tabs.'
						)}
					</p>
				</div>
				<div class="flex gap-2">
					<button class="wf-button" disabled={!topics.length} on:click={() => addItem('ESSAY')}
						>+ {$i18n.t('Essay')}</button
					>
					<button
						class="wf-button"
						disabled={!questionTasks.length}
						on:click={() => addItem('QUESTION')}>+ {$i18n.t('Question Task')}</button
					>
					<button
						class="wf-button"
						disabled={!surveyTasks.length}
						on:click={() => addItem('SURVEY')}>+ {$i18n.t('Survey')}</button
					>
				</div>
			</div>

			{#each editing.definition.items as item, index (item.key)}
				<section class="rounded-2xl border border-gray-100 p-4 dark:border-gray-850">
					<div class="flex flex-wrap items-center gap-2">
						<span
							class="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-semibold dark:bg-gray-800"
						>
							{index + 1}. {$i18n.t(
								item.task_type === 'ESSAY'
									? 'Essay'
									: item.task_type === 'QUESTION'
										? 'Question Task'
										: 'Survey'
							)}
						</span>
						<input
							class="experiment-input !w-auto min-w-52 flex-1"
							bind:value={item.title}
							on:input={changed}
						/>
						<label class="text-xs"
							><input type="checkbox" bind:checked={item.enabled} on:change={changed} />
							{$i18n.t('Enabled')}</label
						>
						<button
							class="wf-button"
							disabled={index === 0}
							on:click={() => move(index, -1)}
							aria-label={$i18n.t('Move up')}>↑</button
						>
						<button
							class="wf-button"
							disabled={index === editing.definition.items.length - 1}
							on:click={() => move(index, 1)}
							aria-label={$i18n.t('Move down')}>↓</button
						>
						<button class="wf-button text-red-600" on:click={() => deleteItem(index)}
							>{$i18n.t('Delete')}</button
						>
					</div>

					{#if item.task_type === 'QUESTION'}
						<label class="experiment-label mt-3 block">
							{$i18n.t('Published Question Task version')}
							<select
								class="experiment-input mt-1"
								value={item.question_task_id ?? ''}
								on:change={(event) => setQuestionTask(item, event.currentTarget.value)}
							>
								{#if item.question_task_id && !questionTasks.some((task) => task.id === item.question_task_id)}
									<option value={item.question_task_id}
										>{$i18n.t('Unavailable or archived task')}</option
									>
								{/if}
								<option value="" disabled>{$i18n.t('Select a Question Task')}</option>
								{#each questionTasks as task}
									<option value={task.id}
										>{task.title} · v{task.version}{newerQuestionVersionExists(task)
											? ` · ${$i18n.t('newer published version available')}`
											: ''}</option
									>
								{/each}
							</select>
						</label>
					{:else if item.task_type === 'SURVEY'}
						<div class="mt-3 grid gap-3 md:grid-cols-3">
							<label class="experiment-label md:col-span-2">
								{$i18n.t('Published Survey Task version')}
								<select
									class="experiment-input mt-1"
									value={item.survey_task_id ?? ''}
									on:change={(event) => setSurveyTask(item, event.currentTarget.value)}
								>
									{#if item.survey_task_id && !surveyTasks.some((task) => task.id === item.survey_task_id)}
										<option value={item.survey_task_id}
											>{$i18n.t('Unavailable or archived task')}</option
										>
									{/if}
									<option value="" disabled>{$i18n.t('Select a Survey Task')}</option>
									{#each surveyTasks as task}
										<option value={task.id}
											>{task.title} · v{task.version}{newerSurveyVersionExists(task)
												? ` · ${$i18n.t('newer published version available')}`
												: ''}</option
										>
									{/each}
								</select>
							</label>
							<label
								class="flex items-center gap-2 self-end rounded-xl bg-gray-50 px-3 py-2 text-xs dark:bg-gray-900"
							>
								<input type="checkbox" bind:checked={item.survey_required} on:change={changed} />
								{$i18n.t('Required stage')}
							</label>
						</div>
					{:else}
						<div class="mt-3 grid gap-3 md:grid-cols-2">
							<label class="experiment-label">
								{$i18n.t('Topic selection')}
								<select
									class="experiment-input mt-1"
									bind:value={item.essay_topic_mode}
									on:change={changed}
								>
									<option value="SPECIFIC">{$i18n.t('Specific topic')}</option>
									<option value="RANDOM_ALL">{$i18n.t('Random from all topics')}</option>
									<option value="RANDOM_SELECTED">{$i18n.t('Random from selected pool')}</option>
								</select>
							</label>
							{#if item.essay_topic_mode === 'SPECIFIC'}
								<label class="experiment-label">
									{$i18n.t('Essay topic')}
									<select
										class="experiment-input mt-1"
										bind:value={item.essay_topic_id}
										on:change={changed}
									>
										{#if item.essay_topic_id && !topics.some((topic) => topic.id === item.essay_topic_id)}
											<option value={item.essay_topic_id}>{$i18n.t('Unavailable topic')}</option>
										{/if}
										<option value="" disabled>{$i18n.t('Select a topic')}</option>
										{#each topics as topic}<option value={topic.id}>{topic.title}</option>{/each}
									</select>
								</label>
							{:else if item.essay_topic_mode === 'RANDOM_ALL'}
								<div
									class="self-end rounded-xl bg-gray-50 px-3 py-2 text-xs text-gray-500 dark:bg-gray-900"
								>
									{$i18n.t('Saving pins the current topic library')} · {topics.length}
									{$i18n.t('topics')}
								</div>
							{/if}
						</div>
						{#if item.essay_topic_mode === 'RANDOM_SELECTED'}
							<div class="mt-3 rounded-xl bg-gray-50 p-3 dark:bg-gray-900">
								<div class="mb-2 text-xs font-medium">
									{$i18n.t('Topic pool — choose at least two')}
								</div>
								<div class="grid gap-2 sm:grid-cols-2">
									{#each topics as topic}
										<label class="flex items-center gap-2 text-xs">
											<input
												type="checkbox"
												checked={(item.essay_topic_ids ?? []).includes(topic.id)}
												on:change={(event) =>
													togglePoolTopic(item, topic.id, event.currentTarget.checked)}
											/>
											{topic.title}
										</label>
									{/each}
								</div>
							</div>
						{/if}
					{/if}
				</section>
			{:else}
				<div
					class="rounded-2xl border border-dashed border-gray-200 py-14 text-center text-sm text-gray-500 dark:border-gray-800"
				>
					{$i18n.t('Add at least one existing resource to make this workflow ready.')}
				</div>
			{/each}
		</div>

		<div on:input={changed} on:change={changed}>
			<ExperimentPerturbations
				bind:conditions={editing.definition.conditions}
				items={conditionItems}
			/>
		</div>
	</div>
{/if}

{#if showApply && editing}
	<div class="fixed inset-0 z-50 flex items-center justify-center p-4">
		<button
			class="absolute inset-0 bg-black/50"
			aria-label={$i18n.t('Close')}
			on:click={() => (showApply = false)}
		></button>
		<div class="relative w-full max-w-lg rounded-2xl bg-white p-5 dark:bg-gray-900">
			<h2 class="text-lg font-semibold">{$i18n.t('Review workflow publication')}</h2>
			<p class="mt-2 text-sm text-gray-500">
				{$i18n.t(
					'This supersedes the current group plan with a new immutable version. Existing participant sessions remain unchanged.'
				)}
			</p>
			<div class="mt-3 rounded-xl bg-gray-50 p-3 text-xs dark:bg-gray-850">
				{editing.definition.items.length}
				{$i18n.t('stages')} ·
				{editing.definition.items.filter((item) => item.task_type === 'ESSAY').length}
				{$i18n.t('essays')} ·
				{editing.definition.items.filter((item) => item.task_type === 'QUESTION').length}
				{$i18n.t('question tasks')} ·
				{editing.definition.items.filter((item) => item.task_type === 'SURVEY').length}
				{$i18n.t('surveys')}
				<div class="mt-2 font-medium">
					{$i18n.t('Current plan')}: v{reviewPlanVersion} → {$i18n.t('New plan')}: v{reviewPlanVersion +
						1}
				</div>
			</div>
			<label class="experiment-label mt-4 block">
				{$i18n.t('Target group')}
				<select
					class="experiment-input mt-1"
					bind:value={selectedGroupId}
					on:change={refreshApplyReview}
				>
					{#each groups.filter((group) => group.permissions?.features?.essay_sidebar) as group}
						<option value={group.id}>{group.name}</option>
					{/each}
				</select>
			</label>
			<div class="mt-5 flex justify-end gap-2">
				<button class="wf-button" on:click={() => (showApply = false)}>{$i18n.t('Cancel')}</button>
				<button class="wf-primary" disabled={!selectedGroupId || applying} on:click={apply}
					>{applying ? $i18n.t('Publishing...') : $i18n.t('Confirm and publish')}</button
				>
			</div>
		</div>
	</div>
{/if}

<style>
	:global(.wf-button) {
		border-radius: 0.65rem;
		background: rgb(243 244 246);
		padding: 0.5rem 0.75rem;
		font-size: 0.75rem;
		font-weight: 500;
	}
	:global(.dark .wf-button) {
		background: rgb(31 41 55);
	}
	:global(.wf-primary) {
		border-radius: 9999px;
		background: black;
		padding: 0.55rem 1rem;
		font-size: 0.75rem;
		font-weight: 500;
		color: white;
	}
	:global(.dark .wf-primary) {
		background: white;
		color: black;
	}
	:global(.wf-button:disabled),
	:global(.wf-primary:disabled) {
		opacity: 0.4;
	}
</style>
