<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import {
		archiveSurveyTask,
		cloneSurveyTask,
		createSurveyTask,
		getSurveyTasks,
		publishSurveyTask,
		updateSurveyTask,
		type SurveyQuestion,
		type SurveyQuestionType,
		type SurveyTask
	} from '$lib/apis/survey-tasks';
	import './experiment-admin.css';

	const i18n: Writable<i18nType> = getContext('i18n');
	let tasks: SurveyTask[] = [];
	let editing: SurveyTask | null = null;
	let title = '';
	let description = '';
	let questions: SurveyQuestion[] = [];
	let loading = true;
	let saving = false;
	let preview = false;

	const defaultQuestion = (
		question_type: SurveyQuestionType = 'SINGLE_CHOICE'
	): SurveyQuestion => ({
		prompt: '',
		description: '',
		question_type,
		required: true,
		enabled: true,
		choices: ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(question_type)
			? [{ text: '' }, { text: '' }]
			: [],
		scale_low_label: question_type === 'SCALE' ? $i18n.t('Low') : null,
		scale_high_label: question_type === 'SCALE' ? $i18n.t('High') : null
	});

	const load = async () => {
		loading = true;
		try {
			tasks = await getSurveyTasks(localStorage.token);
		} catch (error) {
			toast.error(String(error));
		} finally {
			loading = false;
		}
	};

	const reset = () => {
		editing = null;
		title = '';
		description = '';
		questions = [];
	};

	const edit = (task: SurveyTask) => {
		editing = task;
		title = task.title;
		description = task.description;
		questions = structuredClone(task.questions);
		window.scrollTo({ top: 0, behavior: 'smooth' });
	};

	const changeType = (index: number, question_type: SurveyQuestionType) => {
		questions[index] = {
			...defaultQuestion(question_type),
			id: questions[index].id,
			prompt: questions[index].prompt,
			description: questions[index].description,
			required: questions[index].required,
			enabled: questions[index].enabled
		};
		questions = [...questions];
	};

	const move = (index: number, offset: number) => {
		const target = index + offset;
		if (target < 0 || target >= questions.length) return;
		[questions[index], questions[target]] = [questions[target], questions[index]];
		questions = [...questions];
	};

	const payload = () => ({ title, description, questions });
	const save = async () => {
		if (saving || !title.trim()) return;
		saving = true;
		try {
			const task = editing
				? await updateSurveyTask(localStorage.token, editing.id, payload())
				: await createSurveyTask(localStorage.token, payload());
			toast.success($i18n.t('Survey Task draft saved'));
			await load();
			edit(task);
		} catch (error) {
			toast.error(String(error));
		} finally {
			saving = false;
		}
	};

	const publish = async () => {
		if (!editing || editing.status !== 'DRAFT') return;
		try {
			await save();
			const task = await publishSurveyTask(localStorage.token, editing.id);
			await load();
			edit(task);
			toast.success($i18n.t('Survey Task published'));
		} catch (error) {
			toast.error(String(error));
		}
	};

	const clone = async (task: SurveyTask, mode: 'VERSION' | 'DUPLICATE') => {
		try {
			const created = await cloneSurveyTask(localStorage.token, task.id, mode);
			await load();
			edit(created);
			toast.success(
				$i18n.t(mode === 'VERSION' ? 'Editable version created' : 'Survey Task duplicated')
			);
		} catch (error) {
			toast.error(String(error));
		}
	};

	const archive = async (task: SurveyTask) => {
		try {
			await archiveSurveyTask(localStorage.token, task.id);
			if (editing?.id === task.id) reset();
			await load();
		} catch (error) {
			toast.error(String(error));
		}
	};

	onMount(load);
</script>

<div class="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
	<section class="min-w-0 space-y-4">
		<div class="rounded-2xl border border-gray-100 p-4 dark:border-gray-850">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<div>
					<h2 class="font-medium">
						{editing ? $i18n.t('Survey Task') : $i18n.t('New Survey Task')}
					</h2>
					{#if editing}<p class="text-xs text-gray-500">
							{$i18n.t('Version')}
							{editing.version} · {$i18n.t(editing.status)}
							{#if editing.latest_version > editing.version}
								· {$i18n.t('A newer version exists')}{/if}
						</p>{/if}
				</div>
				<div class="flex gap-2">
					<button class="survey-button" on:click={() => (preview = true)}
						>{$i18n.t('Preview')}</button
					>
					<button class="survey-button" on:click={reset}>{$i18n.t('New')}</button>
				</div>
			</div>
			<div class="mt-4 grid gap-3">
				<label class="experiment-label">
					{$i18n.t('Survey title')}
					<input class="experiment-input" bind:value={title} />
				</label>
				<label class="experiment-label">
					{$i18n.t('Survey description or instructions')}
					<textarea class="experiment-input min-h-20" bind:value={description}></textarea>
				</label>
			</div>
			{#if editing?.status !== 'DRAFT'}<div
					class="mt-3 rounded-xl bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950/20 dark:text-amber-200"
				>
					{$i18n.t(
						'Published Survey Tasks are immutable. Create an editable version or duplicate.'
					)}
				</div>{/if}
		</div>

		<fieldset
			disabled={editing?.status !== 'DRAFT' && editing !== null}
			class="space-y-4 disabled:opacity-75"
		>
			{#each questions as question, index (question.id ?? index)}
				<div class="rounded-2xl border border-gray-100 p-4 dark:border-gray-850">
					<div class="flex flex-wrap items-center gap-2">
						<span class="text-xs font-semibold text-gray-500"
							>{$i18n.t('Question')} {index + 1}</span
						>
						<label class="text-xs"
							><input type="checkbox" bind:checked={question.enabled} /> {$i18n.t('Enabled')}</label
						>
						<label class="text-xs"
							><input type="checkbox" bind:checked={question.required} />
							{$i18n.t('Required')}</label
						>
						<select
							class="experiment-input ml-auto !w-auto"
							value={question.question_type}
							on:change={(event) =>
								changeType(index, event.currentTarget.value as SurveyQuestionType)}
						>
							<option value="SINGLE_CHOICE">{$i18n.t('Single choice')}</option>
							<option value="MULTIPLE_SELECT">{$i18n.t('Multiple select')}</option>
							<option value="SCALE">{$i18n.t('1–5 scale')}</option>
							<option value="SHORT_TEXT">{$i18n.t('Short text')}</option>
							<option value="LONG_TEXT">{$i18n.t('Long text')}</option>
						</select>
						<button class="survey-button" disabled={index === 0} on:click={() => move(index, -1)}
							>↑</button
						>
						<button
							class="survey-button"
							disabled={index === questions.length - 1}
							on:click={() => move(index, 1)}>↓</button
						>
						<button
							class="survey-button text-red-600"
							on:click={() => (questions = questions.filter((_, itemIndex) => itemIndex !== index))}
							>{$i18n.t('Delete')}</button
						>
					</div>
					<div class="mt-3 grid gap-3">
						<label class="experiment-label">
							{$i18n.t('Question prompt')}
							<input class="experiment-input" bind:value={question.prompt} />
						</label>
						<label class="experiment-label">
							{$i18n.t('Optional description')}
							<textarea class="experiment-input min-h-20" bind:value={question.description}
							></textarea>
						</label>
					</div>
					{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(question.question_type)}
						<div class="mt-3 space-y-2">
							{#each question.choices as choice, choiceIndex}<div class="flex gap-2">
									<input
										class="experiment-input"
										bind:value={choice.text}
										placeholder={`${$i18n.t('Choice')} ${choiceIndex + 1}`}
									/>
									<button
										class="survey-button"
										disabled={question.choices.length <= 2}
										on:click={() => {
											question.choices = question.choices.filter(
												(_, itemIndex) => itemIndex !== choiceIndex
											);
											questions = [...questions];
										}}>×</button
									>
								</div>{/each}
							<button
								class="survey-button"
								on:click={() => {
									question.choices = [...question.choices, { text: '' }];
									questions = [...questions];
								}}>+ {$i18n.t('Add choice')}</button
							>
						</div>
					{:else if question.question_type === 'SCALE'}
						<div class="mt-3 grid gap-2 sm:grid-cols-2">
							<input
								class="experiment-input"
								bind:value={question.scale_low_label}
								placeholder={$i18n.t('Label for 1')}
							/>
							<input
								class="experiment-input"
								bind:value={question.scale_high_label}
								placeholder={$i18n.t('Label for 5')}
							/>
						</div>
					{/if}
				</div>
			{/each}
			<div class="flex flex-wrap justify-between gap-2">
				<button
					class="survey-button"
					on:click={() => (questions = [...questions, defaultQuestion()])}
					>+ {$i18n.t('Add question')}</button
				>
				{#if !editing || editing.status === 'DRAFT'}<div class="flex gap-2">
						<button class="survey-button" disabled={saving || !title.trim()} on:click={save}
							>{saving ? $i18n.t('Saving...') : $i18n.t('Save draft')}</button
						>
						<button
							class="rounded-lg bg-black px-4 py-2 text-xs font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
							disabled={!editing || questions.length === 0}
							on:click={publish}>{$i18n.t('Publish')}</button
						>
					</div>{/if}
			</div>
		</fieldset>
	</section>

	<aside class="min-w-0">
		<h2 class="mb-2 text-sm font-medium">{$i18n.t('Survey Tasks')}</h2>
		{#if loading}<p class="py-8 text-center text-sm text-gray-500">{$i18n.t('Loading...')}</p>{/if}
		<div class="space-y-2">
			{#each tasks as task}<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-850">
					<button class="w-full min-w-0 text-left" on:click={() => edit(task)}>
						<div class="truncate text-sm font-medium">{task.title}</div>
						<div class="text-xs text-gray-500">
							v{task.version} · {task.questions.length}
							{$i18n.t('questions')} · {$i18n.t(task.status)}
						</div>
					</button>
					<div class="mt-2 flex flex-wrap gap-1">
						<button class="survey-button" on:click={() => clone(task, 'VERSION')}
							>{$i18n.t('Edit as new version')}</button
						>
						<button class="survey-button" on:click={() => clone(task, 'DUPLICATE')}
							>{$i18n.t('Duplicate')}</button
						>
						<button class="survey-button text-red-600" on:click={() => archive(task)}
							>{$i18n.t('Archive')}</button
						>
					</div>
				</div>{:else}{#if !loading}<p class="py-8 text-center text-sm text-gray-500">
						{$i18n.t('No Survey Tasks yet.')}
					</p>{/if}{/each}
		</div>
	</aside>
</div>

{#if preview}<div class="fixed inset-0 z-50 flex items-center justify-center p-4">
		<button
			class="absolute inset-0 bg-black/50"
			aria-label={$i18n.t('Close')}
			on:click={() => (preview = false)}
		></button>
		<div
			class="relative max-h-[90dvh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-5 dark:bg-gray-900"
		>
			<div class="flex justify-between">
				<h2 class="text-lg font-semibold">{title || $i18n.t('Untitled Survey')}</h2>
				<button class="survey-button" on:click={() => (preview = false)}>{$i18n.t('Close')}</button>
			</div>
			<p class="mt-2 whitespace-pre-wrap text-sm text-gray-500">{description}</p>
			{#each questions.filter((question) => question.enabled) as question, index}<div
					class="mt-5 rounded-xl border border-gray-100 p-4 dark:border-gray-800"
				>
					<div class="font-medium">
						{index + 1}. {question.prompt}{#if question.required}<span class="text-red-500">
								*</span
							>{/if}
					</div>
					<p class="mt-1 text-sm text-gray-500">{question.description}</p>
					{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(question.question_type)}<div
							class="mt-3 space-y-2"
						>
							{#each question.choices as choice}<label class="flex gap-2 text-sm"
									><input
										disabled
										type={question.question_type === 'SINGLE_CHOICE' ? 'radio' : 'checkbox'}
									/>
									{choice.text}</label
								>{/each}
						</div>
					{:else if question.question_type === 'SCALE'}<div class="mt-3 flex justify-between gap-2">
							{#each [1, 2, 3, 4, 5] as value}<label class="text-center text-xs"
									><input type="radio" disabled /> {value}</label
								>{/each}
						</div>
					{:else}<textarea
							class="experiment-input mt-3"
							disabled
							rows={question.question_type === 'LONG_TEXT' ? 5 : 2}
						></textarea>{/if}
				</div>{/each}
		</div>
	</div>{/if}

<style>
	:global(.survey-button) {
		border-radius: 0.6rem;
		background: rgb(243 244 246);
		padding: 0.45rem 0.7rem;
		font-size: 0.75rem;
		font-weight: 500;
	}
	:global(.dark .survey-button) {
		background: rgb(31 41 55);
	}
</style>
