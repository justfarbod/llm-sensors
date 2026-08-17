<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { quintOut } from 'svelte/easing';
	import { slide } from 'svelte/transition';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import {
		completeExperimentTask,
		finalizeEssayTask,
		finalizeExperimentTasks,
		finalizeQuestionTask,
		getExperimentTask,
		saveEssayTaskDraft,
		saveQuestionTaskDraft,
		type ExperimentTaskSummary
	} from '$lib/apis/experiments';
	import {
		experimentActiveTaskId,
		experimentCurrent,
		experimentRefresh,
		showEssaySidebar
	} from '$lib/stores';
	import Drawer from '$lib/components/common/Drawer.svelte';
	import MarkdownEditor from '$lib/components/common/MarkdownEditor.svelte';
	import SafeMarkdown from '$lib/components/common/SafeMarkdown.svelte';
	import QuestionImage from '$lib/components/experiment/QuestionImage.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import { firstUnlockedExperimentTask } from '$lib/utils/experiments';
	import { flushExperimentTelemetry } from '$lib/utils/experimentTelemetry';
	import { markdownTextMetrics } from '$lib/utils/markdownEditor';

	const i18n: Writable<i18nType> = getContext('i18n');
	export let onTaskChange: (
		taskId: string,
		previousTaskId: string | null
	) => void | Promise<void> = () => {};
	let selectedTaskId = '';
	let detail: any = null;
	let questionIndex = 0;
	let essayContent = '';
	let showEssayExplanation = true;
	let answers: Record<
		string,
		{ choice_ids: string[]; blank_answers: Record<string, string>; text: string }
	> = {};
	let loading = false;
	let saving = false;
	let savePromise: Promise<boolean> | null = null;
	let submitting = false;
	let saveTimer: ReturnType<typeof setTimeout>;
	let largeScreen = false;
	let sidebarWidth = 520;
	let resizing = false;
	let initialSelectionChecked = false;
	const minSize = 30;
	const maxSize = 65;

	$: tasks = (($experimentCurrent?.tasks ?? []) as ExperimentTaskSummary[]).filter(
		(task) => task.task_type !== 'SURVEY'
	);
	$: selectedSummary = tasks.find((task) => task.id === selectedTaskId);
	$: questions = detail?.question_task?.questions ?? [];
	$: currentQuestion = questions[questionIndex];
	$: essayWordCount = markdownTextMetrics(essayContent).wordCount;
	$: if (initialSelectionChecked && !selectedTaskId) {
		const firstUnlocked = firstUnlockedExperimentTask(tasks);
		if (firstUnlocked) void selectTask(firstUnlocked);
	}

	export const openPane = () => {
		const container = document.getElementById('chat-container');
		if (!container) return;
		const saved = parseInt(localStorage.experimentTaskSidebarSize);
		sidebarWidth = Math.max(
			container.clientWidth * (minSize / 100),
			Math.min(container.clientWidth * (maxSize / 100), saved || 520)
		);
	};

	const resize = (clientX: number) => {
		const container = document.getElementById('chat-container');
		if (!container) return;
		const bounds = container.getBoundingClientRect();
		sidebarWidth = Math.max(
			bounds.width * (minSize / 100),
			Math.min(bounds.width * (maxSize / 100), bounds.right - clientX)
		);
		localStorage.experimentTaskSidebarSize = String(Math.round(sidebarWidth));
	};

	const answerArray = () =>
		questions.map((question: any) => ({
			question_id: question.id,
			choice_ids: answers[question.id]?.choice_ids ?? [],
			blank_answers: Object.entries(answers[question.id]?.blank_answers ?? {}).map(
				([blank_id, value]) => ({ blank_id, value })
			),
			text: answers[question.id]?.text ?? ''
		}));

	const saveCurrent = async () => {
		if (!detail || detail.status === 'FINALIZED') return true;
		if (savePromise) return savePromise;
		savePromise = (async () => {
			saving = true;
			try {
				if (detail.task_type === 'ESSAY')
					await saveEssayTaskDraft(localStorage.token, detail.id, essayContent);
				else await saveQuestionTaskDraft(localStorage.token, detail.id, answerArray());
				return true;
			} catch (error) {
				toast.error(`${$i18n.t('Draft could not be saved')}: ${error}`);
				return false;
			} finally {
				saving = false;
				savePromise = null;
			}
		})();
		return savePromise;
	};

	const changed = () => {
		clearTimeout(saveTimer);
		saveTimer = setTimeout(saveCurrent, 700);
	};

	const selectTask = async (task: ExperimentTaskSummary, skipOutgoingSave = false) => {
		if (task.status === 'LOCKED' || task.id === selectedTaskId) return;
		const previousTaskId = selectedTaskId || null;
		clearTimeout(saveTimer);
		if (!skipOutgoingSave && !(await saveCurrent())) return;
		selectedTaskId = task.id;
		sessionStorage.experimentTaskId = task.id;
		experimentActiveTaskId.set(task.id);
		questionIndex = 0;
		loading = true;
		try {
			detail = await getExperimentTask(localStorage.token, task.id);
			if (detail.task_type === 'ESSAY') {
				essayContent = detail.draft ?? '';
				showEssayExplanation = true;
			} else {
				answers = {};
				for (const question of detail.question_task.questions)
					answers[question.id] = { choice_ids: [], blank_answers: {}, text: '' };
				for (const response of detail.submission?.responses ?? []) {
					answers[response.question_id] = {
						choice_ids: response.selected_choice_ids ?? [],
						blank_answers: Object.fromEntries(
							(response.blank_answers ?? []).map((item: any) => [item.blank_id, item.value])
						),
						text: response.text ?? ''
					};
				}
			}
		} catch (error) {
			toast.error(String(error));
		} finally {
			loading = false;
			await onTaskChange(task.id, previousTaskId);
		}
	};

	const isAnswered = (question: any) => {
		const answer = answers[question.id];
		if (!answer) return false;
		if (['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(question.question_type))
			return answer.choice_ids.length > 0;
		if (question.question_type === 'FILL_BLANK')
			return Object.values(answer.blank_answers).some((value) => value.trim());
		return Boolean(answer.text.trim());
	};

	const choose = (question: any, choiceId: string, checked: boolean) => {
		const answer = answers[question.id];
		answer.choice_ids =
			question.question_type === 'SINGLE_CHOICE'
				? checked
					? [choiceId]
					: []
				: checked
					? [...new Set([...answer.choice_ids, choiceId])]
					: answer.choice_ids.filter((id) => id !== choiceId);
		answers = { ...answers };
		changed();
	};

	const descriptionParts = (text: string) =>
		text.split(/(\{\{[A-Za-z][A-Za-z0-9_]*\}\})/g).filter(Boolean);
	const blankForToken = (question: any, part: string) =>
		question.blanks.find((blank: any) => `{{${blank.key}}}` === part);

	const submitTask = async () => {
		if (!detail || submitting) return;
		const unanswered =
			detail.task_type === 'QUESTION'
				? questions.filter((question: any) => !isAnswered(question)).length
				: 0;
		if (
			unanswered &&
			!window.confirm(
				$i18n.t('{{count}} questions are unanswered. Submit anyway?', { count: unanswered })
			)
		)
			return;
		submitting = true;
		try {
			if (
				$experimentCurrent?.telemetry_extension?.required &&
				!(await flushExperimentTelemetry(false))
			)
				throw new Error($i18n.t('Interaction telemetry could not be saved. Please try again.'));
			clearTimeout(saveTimer);
			if (savePromise && !(await savePromise)) throw new Error($i18n.t('Draft could not be saved'));
			let nextExperiment: any = null;
			if ($experimentCurrent?.progression_mode === 'STRICT_SEQUENTIAL') {
				if (detail.task_type === 'ESSAY')
					nextExperiment = await finalizeEssayTask(localStorage.token, detail.id, essayContent);
				else
					nextExperiment = (
						await finalizeQuestionTask(localStorage.token, detail.id, answerArray())
					).experiment;
			} else if ($experimentCurrent?.progression_mode === 'SEQUENTIAL_REVIEW') {
				if (!(await saveCurrent())) throw new Error($i18n.t('Draft could not be saved'));
				nextExperiment = await completeExperimentTask(localStorage.token, detail.id);
			} else if (!(await saveCurrent())) throw new Error($i18n.t('Draft could not be saved'));
			if (nextExperiment) experimentCurrent.set(nextExperiment);
			detail = { ...detail, status: 'FINALIZED' };
			experimentRefresh.update((value) => value + 1);
			await tick();
			const next =
				tasks.find((task) => task.status === 'ACTIVE' && task.id !== detail.id) ??
				tasks.find((task) => task.status === 'AVAILABLE' && task.id !== detail.id);
			if (next) await selectTask(next, true);
			toast.success($i18n.t('Task progress saved'));
		} catch (error) {
			toast.error(String(error));
		} finally {
			submitting = false;
		}
	};

	const finalSubmit = async () => {
		if (
			!window.confirm($i18n.t('Finalize all tasks? You will not be able to edit them afterward.'))
		)
			return;
		try {
			if (!(await saveCurrent())) return;
			if (
				$experimentCurrent?.telemetry_extension?.required &&
				!(await flushExperimentTelemetry(true))
			)
				throw new Error($i18n.t('Interaction telemetry could not be saved. Please try again.'));
			experimentCurrent.set(await finalizeExperimentTasks(localStorage.token));
			experimentRefresh.update((value) => value + 1);
		} catch (error) {
			toast.error(String(error));
		}
	};

	onMount(() => {
		const media = window.matchMedia('(min-width: 1024px)');
		const handle = async () => {
			largeScreen = media.matches;
			await tick();
			if (largeScreen) openPane();
		};
		media.addEventListener('change', handle);
		handle();
		const remembered = tasks.find(
			(task) => task.id === sessionStorage.experimentTaskId && task.status !== 'LOCKED'
		);
		const first =
			remembered ??
			tasks.find((task) => task.status === 'ACTIVE') ??
			tasks.find((task) => task.status === 'AVAILABLE') ??
			tasks.find((task) => task.status === 'COMPLETED') ??
			tasks.find((task) => task.status === 'FINALIZED');
		if (first) selectTask(first);
		initialSelectionChecked = true;
		return () => {
			clearTimeout(saveTimer);
			media.removeEventListener('change', handle);
		};
	});

	onDestroy(() => experimentActiveTaskId.set(null));
</script>

<svelte:window
	on:pointermove={(event) => resizing && resize(event.clientX)}
	on:pointerup={() => (resizing = false)}
	on:resize={() => largeScreen && openPane()}
/>

{#snippet editor()}
	<div class="flex size-full min-h-0 min-w-0 flex-col overflow-hidden bg-white dark:bg-gray-850">
		<div class="shrink-0 border-b border-gray-100 p-2 dark:border-gray-800">
			<div class="flex items-center justify-between">
				<div class="text-sm font-semibold">{$i18n.t('Experiment Tasks')}</div>
				<button
					class="rounded-lg px-2 py-1 text-xs hover:bg-gray-100 dark:hover:bg-gray-800"
					on:click={() => showEssaySidebar.set(false)}>{$i18n.t('Close')}</button
				>
			</div>
			<div class="mt-2 flex gap-1 overflow-x-auto pb-1">
				{#each tasks as task}<button
						disabled={task.status === 'LOCKED'}
						on:click={() => selectTask(task)}
						class="min-w-fit rounded-lg px-2.5 py-1.5 text-xs {task.id === selectedTaskId
							? 'bg-black text-white dark:bg-white dark:text-black'
							: 'bg-gray-100 dark:bg-gray-800'} disabled:opacity-40"
						>{task.position + 1}. {task.task_type === 'ESSAY'
							? $i18n.t('Essay')
							: $i18n.t('Questions')} · {task.title}</button
					>{/each}
			</div>
		</div>

		{#if loading}<div class="flex flex-1 items-center justify-center text-sm text-gray-500">
				{$i18n.t('Loading...')}
			</div>
		{:else if detail}
			{#if detail.task_type === 'ESSAY'}
				<div
					class="shrink-0 border-b border-gray-100 p-2 transition-colors duration-200 dark:border-gray-800 {showEssayExplanation
						? 'bg-gray-50/60 dark:bg-gray-900/40'
						: 'bg-white dark:bg-gray-850'}"
				>
					<button
						type="button"
						class="group flex w-full items-center justify-between gap-3 rounded-xl px-2 py-1.5 text-left transition-colors hover:bg-gray-100/80 dark:hover:bg-gray-800/80"
						on:click={() => (showEssayExplanation = !showEssayExplanation)}
						aria-expanded={showEssayExplanation}
						aria-controls={`essay-instructions-${detail.id}`}
						aria-label={showEssayExplanation
							? $i18n.t('Collapse essay instructions')
							: $i18n.t('Expand essay instructions')}
					>
						<span class="min-w-0">
							<span class="block text-[10px] font-semibold uppercase tracking-wider text-gray-400">
								{$i18n.t('Essay instructions')}
							</span>
							<span class="block truncate text-sm font-medium text-gray-900 dark:text-white">
								{detail.topic?.title}
							</span>
						</span>
						<span class="flex shrink-0 items-center gap-2">
							<span class="hidden text-[11px] font-medium text-gray-400 sm:inline">
								{showEssayExplanation ? $i18n.t('Hide') : $i18n.t('Show')}
							</span>
							<span
								class="flex size-7 items-center justify-center rounded-full bg-gray-100 text-gray-500 transition-all duration-300 group-hover:bg-gray-200 group-hover:text-gray-700 dark:bg-gray-800 dark:text-gray-400 dark:group-hover:bg-gray-700 dark:group-hover:text-gray-200 {showEssayExplanation
									? 'rotate-180'
									: ''}"
							>
								<ChevronDown className="size-3.5" />
							</span>
						</span>
					</button>
					{#if showEssayExplanation}
						<div
							id={`essay-instructions-${detail.id}`}
							transition:slide={{ duration: 240, easing: quintOut, axis: 'y' }}
						>
							<div
								class="mx-2 mt-1 max-h-[30dvh] overflow-y-auto rounded-xl border border-gray-100 bg-white/80 px-3 py-2.5 text-sm text-gray-500 shadow-xs dark:border-gray-800 dark:bg-gray-850/80"
							>
								<SafeMarkdown
									content={detail.topic?.question ?? ''}
									className="markdown-prose-sm"
								/>
							</div>
						</div>
					{/if}
				</div>
				<MarkdownEditor
					bind:value={essayContent}
					experimentField="essay"
					sessionTaskId={detail.id}
					className="min-h-40 flex-1 !rounded-none !border-0 !ring-0"
					textareaClass="p-4"
					ariaLabel={$i18n.t('Essay')}
					onInput={changed}
					readonly={detail.status === 'FINALIZED'}
					placeholder={$i18n.t('Start writing your essay...')}
				/>
			{:else}
				<div
					class="grid min-h-0 flex-1 grid-cols-[4.5rem_minmax(0,1fr)] overflow-hidden sm:grid-cols-[11rem_minmax(0,1fr)]"
				>
					<nav
						class="overflow-y-auto border-r border-gray-100 p-2 dark:border-gray-800"
						aria-label={$i18n.t('Questions')}
					>
						{#each questions as question, index}<button
								class="mb-1 flex w-full items-center gap-2 rounded-lg p-2 text-left text-xs {index ===
								questionIndex
									? 'bg-gray-100 dark:bg-gray-800'
									: ''}"
								on:click={() => (questionIndex = index)}
								><span
									class="flex size-6 shrink-0 items-center justify-center rounded-full {isAnswered(
										question
									)
										? 'bg-emerald-600 text-white'
										: 'bg-gray-200 dark:bg-gray-700'}">{index + 1}</span
								><span class="hidden truncate sm:block">{question.title}</span></button
							>{/each}
					</nav>
					{#if currentQuestion}<section
							class="min-w-0 overflow-y-auto p-4"
							data-experiment-field="question"
							data-session-task-id={detail.id}
							data-question-id={currentQuestion.id}
							data-submission-id={detail.submission?.id}
						>
							{#if detail.question_task?.description}
								<div
									class="mb-4 rounded-xl bg-gray-50 p-3 text-sm text-gray-600 dark:bg-gray-900 dark:text-gray-300"
								>
									<SafeMarkdown
										content={detail.question_task.description}
										className="markdown-prose-sm"
									/>
								</div>
							{/if}
							<div class="flex flex-wrap items-start justify-between gap-2">
								<h2 class="min-w-0 text-base font-semibold">{currentQuestion.title}</h2>
								<span class="shrink-0 text-xs text-gray-500"
									>{currentQuestion.max_score} {$i18n.t('points')}</span
								>
							</div>
							{#if currentQuestion.question_type === 'FILL_BLANK'}<div
									class="mt-3 whitespace-pre-wrap text-sm leading-8"
								>
									{#each descriptionParts(currentQuestion.description) as part}{#if blankForToken(currentQuestion, part)}{@const blank =
												blankForToken(currentQuestion, part)}<input
												data-question-control="fill_blank"
												class="mx-1 inline-block max-w-full rounded-lg bg-gray-100 px-2 py-1 outline-hidden dark:bg-gray-800"
												value={answers[currentQuestion.id]?.blank_answers[blank.id] ?? ''}
												on:input={(event) => {
													answers[currentQuestion.id].blank_answers[blank.id] =
														event.currentTarget.value;
													answers = { ...answers };
													changed();
												}}
												readonly={detail.status === 'FINALIZED'}
											/>{:else}{part}{/if}{/each}
								</div>{:else}<div class="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
									<SafeMarkdown
										content={currentQuestion.description}
										className="markdown-prose-sm"
									/>
								</div>{/if}
							{#if currentQuestion.image_file_id}<QuestionImage
									fileId={currentQuestion.image_file_id}
									alt={currentQuestion.title}
									className="mt-4 max-h-[50dvh] max-w-full rounded-xl object-contain"
								/>{/if}
							{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(currentQuestion.question_type)}<div
									class="mt-4 space-y-2"
								>
									{#each currentQuestion.choices as choice}<label
											class="flex min-w-0 items-start gap-3 rounded-xl border border-gray-100 p-3 dark:border-gray-800"
											><input
												data-question-control={currentQuestion.question_type === 'SINGLE_CHOICE'
													? 'single_choice'
													: 'multiple_select'}
												class="mt-0.5"
												type={currentQuestion.question_type === 'SINGLE_CHOICE'
													? 'radio'
													: 'checkbox'}
												name={`question-${currentQuestion.id}`}
												checked={answers[currentQuestion.id]?.choice_ids.includes(choice.id)}
												disabled={detail.status === 'FINALIZED'}
												on:change={(event) =>
													choose(currentQuestion, choice.id, event.currentTarget.checked)}
											/><span class="min-w-0 break-words text-sm">{choice.text}</span></label
										>{/each}
								</div>
							{:else if currentQuestion.question_type === 'FREE_TEXT'}<textarea
									data-question-control="free_text"
									class="mt-4 min-h-48 w-full resize-y rounded-xl bg-gray-100 p-3 text-sm outline-hidden dark:bg-gray-800"
									bind:value={answers[currentQuestion.id].text}
									on:input={changed}
									readonly={detail.status === 'FINALIZED'}
									placeholder={$i18n.t('Enter your answer...')}
								></textarea>{/if}
							<div class="mt-5 flex justify-between">
								<button
									class="rounded-lg bg-gray-100 px-3 py-2 text-xs disabled:opacity-40 dark:bg-gray-800"
									disabled={questionIndex === 0}
									on:click={() => (questionIndex -= 1)}>{$i18n.t('Previous')}</button
								><button
									class="rounded-lg bg-gray-100 px-3 py-2 text-xs disabled:opacity-40 dark:bg-gray-800"
									disabled={questionIndex === questions.length - 1}
									on:click={() => (questionIndex += 1)}>{$i18n.t('Next')}</button
								>
							</div>
						</section>{/if}
				</div>
			{/if}
			<div class="shrink-0 border-t border-gray-100 p-3 dark:border-gray-800">
				{#if detail.task_type === 'ESSAY'}
					<div class="mb-2 text-right text-xs text-gray-400">
						{essayWordCount}
						{$i18n.t('words')}
					</div>
				{/if}
				{#if detail.status !== 'FINALIZED'}<button
						class="w-full rounded-xl bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
						disabled={submitting ||
							saving ||
							(detail.task_type === 'ESSAY' && !essayContent.trim())}
						on:click={submitTask}
						>{$experimentCurrent?.progression_mode === 'FREE_NAVIGATION'
							? $i18n.t('Save task')
							: $experimentCurrent?.progression_mode === 'SEQUENTIAL_REVIEW'
								? $i18n.t('Complete and continue')
								: $i18n.t('Submit task')}</button
					>{/if}
				{#if $experimentCurrent?.progression_mode !== 'STRICT_SEQUENTIAL'}<button
						class="mt-2 w-full rounded-xl border border-gray-200 px-4 py-2 text-sm font-medium dark:border-gray-700"
						on:click={finalSubmit}>{$i18n.t('Finalize experiment tasks')}</button
					>{/if}
			</div>
		{/if}
	</div>
{/snippet}

{#if !largeScreen}
	{#if $showEssaySidebar}<Drawer
			show={$showEssaySidebar}
			onClose={() => showEssaySidebar.set(false)}
			className="min-h-[100dvh] !bg-white dark:!bg-gray-850"
			><div class="h-[100dvh]">{@render editor()}</div></Drawer
		>{/if}
{:else if $showEssaySidebar}
	<div
		class="relative z-20 h-full w-px shrink-0 cursor-col-resize border-l border-gray-100 dark:border-gray-800"
		role="separator"
		aria-orientation="vertical"
		on:pointerdown={(event) => {
			event.preventDefault();
			resizing = true;
			resize(event.clientX);
		}}
	>
		<div class="absolute -inset-x-2 inset-y-0"></div>
	</div>
	<aside
		class="relative z-10 h-full min-w-0 shrink-0 overflow-hidden bg-white dark:bg-gray-850"
		style:width={`${sidebarWidth}px`}
	>
		<div class="absolute inset-0 min-h-0 min-w-0">{@render editor()}</div>
	</aside>
{/if}
