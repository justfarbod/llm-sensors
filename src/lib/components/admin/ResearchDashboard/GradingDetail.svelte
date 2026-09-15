<script lang="ts">
	import { getContext } from 'svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	const i18n: any = getContext('i18n');
	export let questionDetail: any;
	export let questionDetailLoading: any;
	export let scoreDrafts: any;
	export let scoreNotes: any;
	export let closeQuestionDetail: any;
	export let saveQuestionScore: any;
	export let retryGrading: any;
</script>

{#if questionDetailLoading || questionDetail}
	<div class="fixed inset-0 z-50 flex justify-end">
		<button
			class="absolute inset-0 bg-black/30"
			aria-label={$i18n.t('Close question submission detail')}
			on:click={closeQuestionDetail}
		></button>
		<aside
			class="relative h-full w-full max-w-3xl overflow-y-auto bg-white p-6 shadow-2xl dark:bg-gray-900"
		>
			<div class="flex items-center justify-between gap-3">
				<div>
					<h2 class="text-xl font-semibold">{$i18n.t('Question submission')}</h2>
					{#if questionDetail}<p class="mt-1 text-sm text-gray-500">
							{questionDetail.task.title} · {questionDetail.participant_id} · {questionDetail.group
								.name}
						</p>{/if}
				</div>
				<button class="research-button" on:click={closeQuestionDetail}>{$i18n.t('Close')}</button>
			</div>
			{#if questionDetailLoading}
				<div class="flex h-64 items-center justify-center"><Spinner /></div>
			{:else}
				<div class="mt-4 rounded-xl bg-gray-50 p-3 text-sm dark:bg-gray-850">
					{$i18n.t('Total score')}:
					<strong
						>{questionDetail.score ?? $i18n.t('Pending')} / {questionDetail.maximum_score}</strong
					>
					{#if questionDetail.score === null}<span class="ml-3 text-xs text-gray-500"
							>{$i18n.t('Provisional')}: {questionDetail.provisional_score} / {questionDetail.maximum_score}</span
						>{/if}
					<span class="ml-3 text-xs text-gray-500">{questionDetail.grading_status}</span>
				</div>
				<div class="mt-4 space-y-4">
					{#each questionDetail.responses ?? [] as response, responseIndex}
						<section class="rounded-2xl border border-gray-100 p-4 dark:border-gray-800">
							{#if response.grading_status === 'FAILED'}
								{@const failedAttempt = [...(response.attempts ?? [])]
									.reverse()
									.find((attempt: any) => attempt.status === 'FAILED')}
								<div
									class="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/20 dark:text-red-200"
								>
									<strong>{$i18n.t('Model scoring problem')}</strong>
									<div class="mt-1 text-xs">
										{$i18n.t(
											'This response has no final score. Retry grading or enter an administrative score.'
										)}
									</div>
									{#if failedAttempt}<div class="mt-1 text-xs">
											{failedAttempt.model_id ?? $i18n.t('Model unavailable')} · {failedAttempt.error_code ??
												$i18n.t('Provider error')}
										</div>{/if}
								</div>{/if}
							<div class="flex flex-wrap items-start justify-between gap-2">
								<div>
									<h3 class="font-medium">{responseIndex + 1}. {response.question.title}</h3>
									<p class="mt-1 whitespace-pre-wrap text-sm text-gray-500">
										{response.question.description}
									</p>
								</div>
								<span class="rounded-full bg-gray-100 px-2 py-1 text-xs dark:bg-gray-800"
									>{response.grading_status}</span
								>
							</div>

							<div class="mt-3 grid gap-3 sm:grid-cols-2">
								<div class="rounded-xl bg-gray-50 p-3 text-sm dark:bg-gray-850">
									<div class="mb-2 text-xs font-semibold uppercase text-gray-400">
										{$i18n.t('Participant answer')}
									</div>
									{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(response.question.question_type)}
										<ul class="space-y-1">
											{#each response.question.choices.filter( (choice: any) => response.selected_choice_ids.includes(choice.id) ) as choice}<li
												>
													{choice.text}
												</li>{:else}<li class="text-gray-400">{$i18n.t('No answer')}</li>{/each}
										</ul>
									{:else if response.question.question_type === 'FILL_BLANK'}
										<ul class="space-y-1">
											{#each response.blank_answers as answer}<li>
													<strong
														>{response.question.blanks.find(
															(blank: any) => blank.id === answer.blank_id
														)?.key}:</strong
													>
													{answer.answer || $i18n.t('No answer')}
												</li>{/each}
										</ul>
									{:else}
										<p class="whitespace-pre-wrap">
											{response.text_answer || $i18n.t('No answer')}
										</p>
									{/if}
								</div>
								<div class="rounded-xl bg-emerald-50 p-3 text-sm dark:bg-emerald-950/20">
									<div
										class="mb-2 text-xs font-semibold uppercase text-emerald-700 dark:text-emerald-300"
									>
										{$i18n.t('Answer key')}
									</div>
									{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(response.question.question_type)}
										<ul class="space-y-1">
											{#each response.question.choices.filter((choice: any) => choice.is_correct) as choice}<li
												>
													{choice.text}
												</li>{/each}
										</ul>
									{:else if response.question.question_type === 'FILL_BLANK'}
										<ul class="space-y-1">
											{#each response.question.blanks as blank}<li>
													<strong>{blank.key}:</strong>
													{blank.accepted_answers.join(', ')}
												</li>{/each}
										</ul>
									{:else}
										<p class="whitespace-pre-wrap">
											{response.question.expected_answer || $i18n.t('Manual review')}
										</p>
									{/if}
								</div>
							</div>

							{#if response.rationale}<p
									class="mt-3 rounded-xl bg-blue-50 p-3 text-sm dark:bg-blue-950/20"
								>
									<strong>{$i18n.t('Grading rationale')}:</strong>
									{response.rationale}
								</p>{/if}
							<div class="mt-3 flex flex-wrap items-end gap-2">
								<label class="text-xs text-gray-500"
									>{$i18n.t('Score')}<input
										class="research-input ml-2 !w-24"
										type="number"
										min="0"
										max={response.question.max_score}
										step="0.01"
										value={scoreDrafts[response.id] ?? 0}
										on:input={(event) =>
											(scoreDrafts = {
												...scoreDrafts,
												[response.id]: Number(event.currentTarget.value)
											})}
									/></label
								>
								<input
									class="research-input min-w-52 flex-1"
									value={scoreNotes[response.id] ?? ''}
									on:input={(event) => (scoreNotes[response.id] = event.currentTarget.value)}
									placeholder={$i18n.t('Optional override note')}
								/>
								<button class="research-button" on:click={() => saveQuestionScore(response.id)}
									>{$i18n.t('Save score')}</button
								>
								{#if response.grading_method === 'LLM_ASSISTED' || response.attempts?.some((attempt: any) => attempt.method === 'LLM_ASSISTED')}<button
										class="research-button"
										disabled={['PENDING', 'RUNNING'].includes(response.grading_status)}
										on:click={() => retryGrading(response.id)}
										>{$i18n.t('Retry LLM grading')}</button
									>{/if}
							</div>

							{#if response.attempts?.length || response.overrides?.length}
								<details class="mt-3 text-xs">
									<summary class="cursor-pointer font-medium">{$i18n.t('Grading history')}</summary>
									<div class="mt-2 space-y-2">
										{#each response.attempts ?? [] as attempt}<div
												class="rounded-lg bg-gray-50 p-2 dark:bg-gray-850"
											>
												{$i18n.t('Attempt')} · {attempt.status} · {attempt.model_id ??
													attempt.method} · {attempt.awarded_score ?? '—'}{attempt.error_code
													? ` · ${attempt.error_code}`
													: ''}
											</div>{/each}
										{#each response.overrides ?? [] as override}<div
												class="rounded-lg bg-gray-50 p-2 dark:bg-gray-850"
											>
												{$i18n.t('Override')} · {override.previous_score ?? '—'} → {override.new_score}{override.note
													? ` · ${override.note}`
													: ''}
											</div>{/each}
									</div>
								</details>
							{/if}
						</section>
					{/each}
				</div>
			{/if}
		</aside>
	</div>
{/if}
