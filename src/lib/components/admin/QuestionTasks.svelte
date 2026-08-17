<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import {
		archiveQuestionTask,
		cloneQuestionTask,
		createQuestionTask,
		getQuestionTasks,
		publishQuestionTask,
		updateQuestionTask,
		uploadQuestionImage,
		type QuestionDefinition,
		type QuestionTask,
		type QuestionType
	} from '$lib/apis/question-tasks';
	import QuestionImage from '$lib/components/experiment/QuestionImage.svelte';
	import './experiment-admin.css';

	const i18n: Writable<i18nType> = getContext('i18n');
	let tasks: QuestionTask[] = [];
	let editingId: string | null = null;
	let title = '';
	let description = '';
	let questions: QuestionDefinition[] = [];
	let loading = true;
	let saving = false;
	let preview = false;
	$: editingTask = tasks.find((task) => task.id === editingId) ?? null;

	const defaultQuestion = (question_type: QuestionType = 'SINGLE_CHOICE'): QuestionDefinition => ({
		title: '',
		description: '',
		question_type,
		max_score: 1,
		grading_mode: question_type === 'FREE_TEXT' ? 'MANUAL' : 'AUTOMATIC',
		choices: ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(question_type)
			? [
					{ text: '', is_correct: true },
					{ text: '', is_correct: false }
				]
			: [],
		blanks: question_type === 'FILL_BLANK' ? [{ key: 'blank_1', accepted_answers: [''] }] : [],
		expected_answer: '',
		strictness: 'BALANCED',
		case_sensitive: false
	});

	const load = async () => {
		loading = true;
		try {
			tasks = await getQuestionTasks(localStorage.token);
		} catch (error) {
			toast.error(String(error));
		} finally {
			loading = false;
		}
	};

	const reset = () => {
		editingId = null;
		title = '';
		description = '';
		questions = [];
	};

	const edit = (task: QuestionTask) => {
		editingId = task.id;
		title = task.title;
		description = task.description;
		questions = structuredClone(task.questions);
		window.scrollTo({ top: 0, behavior: 'smooth' });
	};

	const changeType = (index: number, type: QuestionType) => {
		questions[index] = {
			...defaultQuestion(type),
			id: questions[index].id,
			title: questions[index].title
		};
		questions = [...questions];
	};

	const move = (index: number, offset: number) => {
		const target = index + offset;
		if (target < 0 || target >= questions.length) return;
		[questions[index], questions[target]] = [questions[target], questions[index]];
		questions = [...questions];
	};

	const markChoice = (question: QuestionDefinition, choiceIndex: number, checked: boolean) => {
		if (question.question_type === 'SINGLE_CHOICE') {
			question.choices.forEach((choice, index) => (choice.is_correct = index === choiceIndex));
		} else {
			question.choices[choiceIndex].is_correct = checked;
		}
		questions = [...questions];
	};

	const addBlank = (question: QuestionDefinition) => {
		const key = `blank_${question.blanks.length + 1}`;
		question.blanks = [...question.blanks, { key, accepted_answers: [''] }];
		question.description = `${question.description}${question.description ? ' ' : ''}{{${key}}}`;
		questions = [...questions];
	};

	const removeBlank = (question: QuestionDefinition, blankIndex: number) => {
		const [blank] = question.blanks.splice(blankIndex, 1);
		if (blank)
			question.description = question.description.replaceAll(`{{${blank.key}}}`, '').trim();
		question.blanks = [...question.blanks];
		questions = [...questions];
	};

	const uploadImage = async (question: QuestionDefinition, file?: File) => {
		if (!file) return;
		try {
			const image = await uploadQuestionImage(localStorage.token, file);
			question.image_file_id = image.id;
			questions = [...questions];
		} catch (error) {
			toast.error(String(error));
		}
	};

	const strictnessHelp = (strictness?: string | null) =>
		strictness === 'LENIENT'
			? $i18n.t(
					'Rewards the correct core meaning and tolerates minor errors or non-essential omissions.'
				)
			: strictness === 'STRICT'
				? $i18n.t(
						'Requires every essential element and penalizes omissions, unsupported claims, and factual errors.'
					)
				: $i18n.t(
						'Requires the main concepts and deducts proportionally for meaningful omissions or errors.'
					);
	const descriptionParts = (text: string) =>
		text.split(/(\{\{[A-Za-z][A-Za-z0-9_]*\}\})/g).filter(Boolean);
	const blankForToken = (question: QuestionDefinition, part: string) =>
		question.blanks.find((blank) => `{{${blank.key}}}` === part);

	const payload = () => ({ title, description, questions });
	const save = async () => {
		if (saving) return;
		saving = true;
		try {
			const task = editingId
				? await updateQuestionTask(localStorage.token, editingId, payload())
				: await createQuestionTask(localStorage.token, payload());
			toast.success($i18n.t('Question Task draft saved'));
			await load();
			edit(task);
		} catch (error) {
			toast.error(String(error));
		} finally {
			saving = false;
		}
	};

	const publish = async () => {
		if (!editingId) return;
		try {
			await save();
			await publishQuestionTask(localStorage.token, editingId);
			toast.success($i18n.t('Question Task published'));
			await load();
		} catch (error) {
			toast.error(String(error));
		}
	};

	const archive = async (taskId: string) => {
		try {
			await archiveQuestionTask(localStorage.token, taskId);
			if (editingId === taskId) reset();
			await load();
			toast.success($i18n.t('Question Task archived'));
		} catch (error) {
			toast.error(String(error));
		}
	};

	const clone = async (task: QuestionTask, mode: 'VERSION' | 'DUPLICATE') => {
		try {
			const created = await cloneQuestionTask(localStorage.token, task.id, mode);
			await load();
			edit(created);
			toast.success(
				$i18n.t(mode === 'VERSION' ? 'Editable version created' : 'Question Task duplicated')
			);
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
				<h2 class="font-medium">
					{editingId ? $i18n.t('Edit Question Task') : $i18n.t('New Question Task')}
				</h2>
				<div class="flex gap-2">
					<button class="task-button" on:click={() => (preview = true)}>{$i18n.t('Preview')}</button
					>
					<button class="task-button" on:click={reset}>{$i18n.t('New')}</button>
				</div>
			</div>
			<div class="mt-4 grid gap-3">
				<label class="experiment-label">
					{$i18n.t('Task title')}
					<input class="experiment-input" bind:value={title} />
				</label>
				<label class="experiment-label">
					{$i18n.t('Task description or instructions')}
					<textarea class="experiment-input min-h-20" bind:value={description}></textarea>
				</label>
			</div>
			{#if editingTask}<p class="mt-2 text-xs text-gray-500">
					{$i18n.t('Version')}
					{editingTask.version} · {$i18n.t(
						editingTask.status
					)}{#if editingTask.latest_version > editingTask.version}
						· {$i18n.t('A newer version exists')}{/if}
				</p>{/if}
			{#if editingTask && editingTask.status !== 'DRAFT'}<div
					class="mt-3 rounded-xl bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950/20 dark:text-amber-200"
				>
					{$i18n.t(
						'Published Question Tasks are immutable. Create an editable version or duplicate.'
					)}
				</div>{/if}
		</div>

		<fieldset
			disabled={editingTask !== null && editingTask.status !== 'DRAFT'}
			class="contents disabled:opacity-75"
		>
			{#each questions as question, index (question.id ?? index)}
				<div class="rounded-2xl border border-gray-100 p-4 dark:border-gray-850">
					<div class="flex flex-wrap items-center gap-2">
						<span class="text-xs font-semibold text-gray-500"
							>{$i18n.t('Question')} {index + 1}</span
						>
						<select
							class="experiment-input ml-auto !w-auto"
							value={question.question_type}
							on:change={(event) => changeType(index, event.currentTarget.value as QuestionType)}
						>
							<option value="SINGLE_CHOICE">{$i18n.t('Single choice')}</option>
							<option value="MULTIPLE_SELECT">{$i18n.t('Multiple select')}</option>
							<option value="FILL_BLANK">{$i18n.t('Fill in the blank')}</option>
							<option value="FREE_TEXT">{$i18n.t('Free text')}</option>
						</select>
						<button class="task-button" disabled={index === 0} on:click={() => move(index, -1)}
							>↑</button
						>
						<button
							class="task-button"
							disabled={index === questions.length - 1}
							on:click={() => move(index, 1)}>↓</button
						>
						<button
							class="task-button text-red-600"
							on:click={() => (questions = questions.filter((_, itemIndex) => itemIndex !== index))}
							>{$i18n.t('Delete')}</button
						>
					</div>
					<div class="mt-3 grid gap-3">
						<label class="experiment-label">
							{$i18n.t('Question title')}
							<input class="experiment-input" bind:value={question.title} />
						</label>
						<label class="experiment-label">
							{$i18n.t('Description or instructions')}
							<textarea
								class="experiment-input min-h-24"
								bind:value={question.description}
								placeholder={question.question_type === 'FILL_BLANK'
									? $i18n.t('Use blank tokens such as {{blank_1}}')
									: ''}
							></textarea>
						</label>
					</div>
					<div class="mt-2 flex flex-wrap items-center gap-3">
						<label class="text-xs"
							>{$i18n.t('Points')}
							<input
								class="experiment-input ml-1 !w-24"
								type="number"
								min="0.01"
								max="10000"
								step="0.01"
								bind:value={question.max_score}
							/></label
						>
						<label class="task-button cursor-pointer"
							>{$i18n.t('Upload image')}<input
								class="hidden"
								type="file"
								accept="image/png,image/jpeg,image/webp,image/gif"
								on:change={(event) => uploadImage(question, event.currentTarget.files?.[0])}
							/></label
						>
						{#if question.image_file_id}<button
								class="task-button"
								on:click={() => {
									question.image_file_id = null;
									questions = [...questions];
								}}>{$i18n.t('Remove image')}</button
							>{/if}
					</div>
					{#if question.image_file_id}<QuestionImage
							fileId={question.image_file_id}
							alt={question.title}
							className="mt-3 max-h-64 max-w-full rounded-xl object-contain"
						/>{/if}

					{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(question.question_type)}
						<div class="mt-3 space-y-2">
							{#each question.choices as choice, choiceIndex}
								<div class="flex min-w-0 items-center gap-2">
									<input
										type={question.question_type === 'SINGLE_CHOICE' ? 'radio' : 'checkbox'}
										name={`correct-${index}`}
										checked={choice.is_correct}
										on:change={(event) =>
											markChoice(question, choiceIndex, event.currentTarget.checked)}
										aria-label={$i18n.t('Correct answer')}
									/>
									<input
										class="experiment-input"
										bind:value={choice.text}
										placeholder={`${$i18n.t('Choice')} ${choiceIndex + 1}`}
									/>
									<button
										class="task-button"
										disabled={question.choices.length <= 2}
										on:click={() => {
											question.choices = question.choices.filter((_, i) => i !== choiceIndex);
											questions = [...questions];
										}}>×</button
									>
								</div>
							{/each}
							<button
								class="task-button"
								on:click={() => {
									question.choices = [...question.choices, { text: '', is_correct: false }];
									questions = [...questions];
								}}>+ {$i18n.t('Add choice')}</button
							>
						</div>
					{:else if question.question_type === 'FILL_BLANK'}
						<div class="mt-3 space-y-3">
							{#each question.blanks as blank, blankIndex}
								<div class="rounded-xl bg-gray-50 p-3 dark:bg-gray-900">
									<div class="flex items-center justify-between gap-2">
										<div class="text-xs font-medium">{`{{${blank.key}}}`}</div>
										<button
											class="task-button text-red-600"
											on:click={() => removeBlank(question, blankIndex)}
											>{$i18n.t('Delete blank')}</button
										>
									</div>
									{#each blank.accepted_answers as answer, answerIndex}
										<div class="mt-2 flex gap-2">
											<input
												class="experiment-input"
												bind:value={blank.accepted_answers[answerIndex]}
												placeholder={$i18n.t('Accepted answer')}
											/><button
												class="task-button"
												disabled={blank.accepted_answers.length === 1}
												on:click={() => {
													blank.accepted_answers = blank.accepted_answers.filter(
														(_, i) => i !== answerIndex
													);
													questions = [...questions];
												}}>×</button
											>
										</div>
									{/each}
									<button
										class="task-button mt-2"
										on:click={() => {
											blank.accepted_answers = [...blank.accepted_answers, ''];
											questions = [...questions];
										}}>+ {$i18n.t('Accepted answer')}</button
									>
								</div>
							{/each}
							<button class="task-button" on:click={() => addBlank(question)}
								>+ {$i18n.t('Add blank')}</button
							>
							<label class="ml-3 text-xs"
								><input type="checkbox" bind:checked={question.case_sensitive} />
								{$i18n.t('Case-sensitive matching')}</label
							>
						</div>
					{:else}
						<div class="mt-3 flex flex-wrap gap-3">
							<select class="experiment-input !w-auto" bind:value={question.grading_mode}
								><option value="MANUAL">{$i18n.t('Manual grading')}</option><option
									value="LLM_ASSISTED">{$i18n.t('LLM-assisted grading')}</option
								></select
							>
							{#if question.grading_mode === 'LLM_ASSISTED'}
								<select class="experiment-input !w-auto" bind:value={question.strictness}
									><option value="LENIENT">{$i18n.t('Lenient')}</option><option value="BALANCED"
										>{$i18n.t('Balanced')}</option
									><option value="STRICT">{$i18n.t('Strict')}</option></select
								>
								<p class="self-center text-xs text-gray-500">
									{strictnessHelp(question.strictness)}
								</p>
								<textarea
									class="experiment-input min-h-24 basis-full"
									bind:value={question.expected_answer}
									placeholder={$i18n.t('Sample or expected answer')}
								></textarea>
							{/if}
						</div>
					{/if}
					{#if question.question_type !== 'FREE_TEXT'}
						<label class="mt-3 block text-xs"
							><input
								type="checkbox"
								checked={question.grading_mode === 'AUTOMATIC'}
								on:change={(event) => {
									question.grading_mode = event.currentTarget.checked ? 'AUTOMATIC' : 'MANUAL';
									questions = [...questions];
								}}
							/>
							{$i18n.t('Grade automatically')}</label
						>
						{#if question.question_type === 'MULTIPLE_SELECT'}<p class="mt-2 text-xs text-gray-500">
								{$i18n.t(
									'Partial credit rewards correct selections and subtracts incorrect selections; selecting every option earns zero.'
								)}
							</p>{/if}
					{/if}
				</div>
			{/each}

			<div class="flex flex-wrap justify-between gap-2">
				<button class="task-button" on:click={() => (questions = [...questions, defaultQuestion()])}
					>+ {$i18n.t('Add question')}</button
				>
				{#if !editingTask || editingTask.status === 'DRAFT'}<div class="flex gap-2">
						<button class="task-button" disabled={saving || !title.trim()} on:click={save}
							>{saving ? $i18n.t('Saving...') : $i18n.t('Save draft')}</button
						><button
							class="rounded-lg bg-black px-4 py-2 text-xs font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
							disabled={!editingId || questions.length === 0}
							on:click={publish}>{$i18n.t('Publish')}</button
						>
					</div>{/if}
			</div>
		</fieldset>
	</section>

	<aside class="min-w-0">
		<h2 class="mb-2 text-sm font-medium">{$i18n.t('Question Tasks')}</h2>
		{#if loading}<p class="py-8 text-center text-sm text-gray-500">{$i18n.t('Loading...')}</p>{/if}
		<div class="space-y-2">
			{#each tasks as task}
				<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-850">
					<div class="flex items-start justify-between gap-2">
						<button class="min-w-0 text-left" on:click={() => edit(task)}
							><div class="truncate text-sm font-medium">{task.title}</div>
							<div class="text-xs text-gray-500">
								v{task.version} · {task.questions.length}
								{$i18n.t('questions')} · {$i18n.t(task.status)}
							</div></button
						>
					</div>
					<div class="mt-2 flex flex-wrap gap-1">
						<button class="task-button" on:click={() => clone(task, 'VERSION')}
							>{$i18n.t('Edit as new version')}</button
						>
						<button class="task-button" on:click={() => clone(task, 'DUPLICATE')}
							>{$i18n.t('Duplicate')}</button
						>
						<button class="task-button text-red-600" on:click={() => archive(task.id)}
							>{$i18n.t('Archive')}</button
						>
					</div>
				</div>
			{:else}
				{#if !loading}<p class="py-8 text-center text-sm text-gray-500">
						{$i18n.t('No Question Tasks yet.')}
					</p>{/if}
			{/each}
		</div>
	</aside>
</div>

{#if preview}
	<div class="fixed inset-0 z-50 flex items-center justify-center p-4">
		<button
			class="absolute inset-0 bg-black/50"
			aria-label={$i18n.t('Close')}
			on:click={() => (preview = false)}
		></button>
		<div
			class="relative max-h-[90dvh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white p-5 dark:bg-gray-900"
		>
			<div class="flex justify-between">
				<h2 class="text-lg font-semibold">{title || $i18n.t('Untitled Question Task')}</h2>
				<button class="task-button" on:click={() => (preview = false)}>{$i18n.t('Close')}</button>
			</div>
			<p class="mt-2 whitespace-pre-wrap text-sm text-gray-500">{description}</p>
			{#each questions as question, index}
				<div class="mt-5 rounded-xl border border-gray-100 p-4 dark:border-gray-800">
					<div class="font-medium">
						{index + 1}. {question.title}
						<span class="text-xs text-gray-400">({question.max_score} {$i18n.t('points')})</span>
					</div>
					{#if question.question_type === 'FILL_BLANK'}
						<div class="mt-2 whitespace-pre-wrap text-sm leading-8">
							{#each descriptionParts(question.description) as part}{#if blankForToken(question, part)}<input
										class="mx-1 inline-block max-w-full rounded-lg bg-gray-100 px-2 py-1 dark:bg-gray-800"
										disabled
										aria-label={$i18n.t('Blank answer')}
									/>{:else}{part}{/if}{/each}
						</div>
					{:else}
						<p class="mt-2 whitespace-pre-wrap text-sm">{question.description}</p>
					{/if}
					{#if question.image_file_id}<QuestionImage
							fileId={question.image_file_id}
							alt={question.title}
							className="mt-3 max-h-72 max-w-full rounded-xl object-contain"
						/>{/if}
					{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(question.question_type)}
						<div class="mt-3 space-y-2">
							{#each question.choices as choice}<label
									class="flex items-start gap-2 rounded-xl border border-gray-100 p-3 text-sm dark:border-gray-800"
									><input
										type={question.question_type === 'SINGLE_CHOICE' ? 'radio' : 'checkbox'}
										disabled
									/>
									{choice.text}</label
								>{/each}
						</div>
					{:else if question.question_type === 'FREE_TEXT'}
						<textarea
							class="experiment-input mt-3 min-h-28"
							disabled
							placeholder={$i18n.t('Participant answer')}
						></textarea>
					{/if}
				</div>
			{/each}
		</div>
	</div>
{/if}

<style>
	:global(.task-button) {
		border-radius: 0.6rem;
		background: rgb(243 244 246);
		padding: 0.45rem 0.7rem;
		font-size: 0.75rem;
		font-weight: 500;
	}
	:global(.dark .task-button) {
		background: rgb(31 41 55);
	}
</style>
