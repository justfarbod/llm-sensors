<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import {
		getExperimentTask,
		skipExperimentSurvey,
		submitExperimentSurvey
	} from '$lib/apis/experiments';
	import { experimentCurrent, experimentRefresh } from '$lib/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n: Writable<i18nType> = getContext('i18n');
	$: activeSurvey = ($experimentCurrent?.tasks ?? []).find(
		(task) => task.task_type === 'SURVEY' && task.status === 'ACTIVE'
	);
	let loadedId = '';
	let detail: any = null;
	let loading = false;
	let submitting = false;
	let answers: Record<string, { choice_ids: string[]; text: string; scale: number | null }> = {};

	const load = async (id: string) => {
		loadedId = id;
		loading = true;
		detail = null;
		try {
			detail = await getExperimentTask(localStorage.token, id);
			answers = {};
			for (const question of detail.survey_task.questions)
				answers[question.id] = { choice_ids: [], text: '', scale: null };
			for (const response of detail.submission?.responses ?? [])
				answers[response.question_id] = {
					choice_ids: response.choice_ids ?? [],
					text: response.text ?? '',
					scale: response.scale ?? null
				};
		} catch (error) {
			toast.error(String(error));
		} finally {
			loading = false;
		}
	};

	$: if (activeSurvey?.id && activeSurvey.id !== loadedId) load(activeSurvey.id);
	$: if (!activeSurvey && loadedId) {
		loadedId = '';
		detail = null;
	}

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
	};

	const answerArray = () =>
		detail.survey_task.questions.map((question: any) => ({
			question_id: question.id,
			choice_ids: answers[question.id]?.choice_ids ?? [],
			text: answers[question.id]?.text || null,
			scale: answers[question.id]?.scale ?? null
		}));

	const applyResult = (result: any) => {
		experimentCurrent.set(result.experiment);
		experimentRefresh.update((value) => value + 1);
		loadedId = '';
		detail = null;
	};

	const submit = async () => {
		if (!detail || submitting) return;
		submitting = true;
		try {
			applyResult(await submitExperimentSurvey(localStorage.token, detail.id, answerArray()));
			toast.success($i18n.t('Survey submitted'));
		} catch (error) {
			toast.error(String(error));
		} finally {
			submitting = false;
		}
	};

	const skip = async () => {
		if (!detail || detail.required || submitting) return;
		submitting = true;
		try {
			applyResult(await skipExperimentSurvey(localStorage.token, detail.id));
			toast.info($i18n.t('Optional survey skipped'));
		} catch (error) {
			toast.error(String(error));
		} finally {
			submitting = false;
		}
	};
</script>

{#if activeSurvey}
	<div
		class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/55 p-3"
		role="dialog"
		aria-modal="true"
		aria-label={$i18n.t('Experiment survey')}
	>
		<div
			class="flex max-h-[94dvh] w-full max-w-2xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl dark:bg-gray-900"
		>
			{#if loading || !detail}
				<div class="flex h-80 items-center justify-center"><Spinner /></div>
			{:else}
				<div class="border-b border-gray-100 p-5 dark:border-gray-800">
					<div class="flex items-start justify-between gap-3">
						<div>
							<h2 class="text-xl font-semibold">{detail.survey_task.title}</h2>
							<p class="mt-1 whitespace-pre-wrap text-sm text-gray-500">
								{detail.survey_task.description}
							</p>
						</div>
						{#if !detail.required}<button
								class="rounded-lg bg-gray-100 px-3 py-2 text-xs dark:bg-gray-800"
								disabled={submitting}
								on:click={skip}>{$i18n.t('Skip optional survey')}</button
							>{/if}
					</div>
				</div>
				<form
					class="min-h-0 flex-1 overflow-y-auto p-5"
					on:submit={(event) => {
						event.preventDefault();
						submit();
					}}
				>
					<div class="space-y-6">
						{#each detail.survey_task.questions as question, index}<section>
								<label class="block text-sm font-medium" for={`survey-${question.id}`}
									>{index + 1}. {question.prompt}{#if question.required}<span class="text-red-500">
											*</span
										>{/if}</label
								>
								{#if question.description}<p class="mt-1 whitespace-pre-wrap text-xs text-gray-500">
										{question.description}
									</p>{/if}
								{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(question.question_type)}<div
										class="mt-3 space-y-2"
									>
										{#each question.choices as choice}<label
												class="flex items-start gap-3 rounded-xl border border-gray-100 p-3 text-sm dark:border-gray-800"
												><input
													type={question.question_type === 'SINGLE_CHOICE' ? 'radio' : 'checkbox'}
													name={`survey-${question.id}`}
													checked={answers[question.id]?.choice_ids.includes(choice.id)}
													on:change={(event) =>
														choose(question, choice.id, event.currentTarget.checked)}
												/> <span>{choice.text}</span></label
											>{/each}
									</div>
								{:else if question.question_type === 'SCALE'}<div
										class="mt-3 grid grid-cols-5 gap-2"
									>
										{#each [1, 2, 3, 4, 5] as value}<label
												class="rounded-xl border border-gray-100 p-2 text-center text-xs dark:border-gray-800"
												><input
													class="block w-full"
													type="radio"
													name={`survey-${question.id}`}
													{value}
													checked={answers[question.id]?.scale === value}
													on:change={() => {
														answers[question.id].scale = value;
														answers = { ...answers };
													}}
												/><span>{value}</span></label
											>{/each}
										<div class="col-span-5 flex justify-between text-[11px] text-gray-500">
											<span>{question.scale_low_label}</span><span>{question.scale_high_label}</span
											>
										</div>
									</div>
								{:else if question.question_type === 'SHORT_TEXT'}<input
										id={`survey-${question.id}`}
										class="mt-3 w-full rounded-xl bg-gray-100 px-3 py-2 text-sm outline-none dark:bg-gray-800"
										maxlength="500"
										bind:value={answers[question.id].text}
									/>
								{:else}<textarea
										id={`survey-${question.id}`}
										class="mt-3 min-h-32 w-full rounded-xl bg-gray-100 p-3 text-sm outline-none dark:bg-gray-800"
										maxlength="4000"
										bind:value={answers[question.id].text}
									></textarea>{/if}
							</section>{/each}
					</div>
					<button
						class="mt-7 w-full rounded-xl bg-black px-4 py-3 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
						disabled={submitting}
						type="submit">{submitting ? $i18n.t('Submitting...') : $i18n.t('Submit survey')}</button
					>
				</form>
			{/if}
		</div>
	</div>
{/if}
