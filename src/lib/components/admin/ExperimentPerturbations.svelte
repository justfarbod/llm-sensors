<script lang="ts">
	import { getContext, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import type { ExperimentCondition, PlanItem } from '$lib/apis/experiment-plans';
	import {
		deterministicPreviewChunks,
		waitUntilPreviewDeadline
	} from '$lib/utils/experimentTimingPreview';
	import ExperimentActivationControls from './ExperimentActivationControls.svelte';
	import './experiment-admin.css';

	export let conditions: ExperimentCondition[] = [];
	export let items: PlanItem[] = [];
	const i18n: Writable<i18nType> = getContext('i18n');
	let preview: ExperimentCondition | null = null;
	let timingPreviewCondition: ExperimentCondition | null = null;
	let timingPreviewText = '';
	let timingPreviewRun = 0;
	let timingPreviewState: 'IDLE' | 'WAITING' | 'STREAMING' | 'COMPLETE' | 'CANCELLED' = 'IDLE';
	let timingPreviewElapsedMs = 0;
	let timingPreviewTotalMs = 0;
	let timingClock: ReturnType<typeof setInterval> | null = null;

	const activation = () => ({
		mode: 'EVERY_REQUEST' as const,
		count: null,
		range_start: null,
		range_end: null,
		probability: 1,
		scope: 'ALL_TASKS' as const,
		plan_item_ids: []
	});
	const normalSettings = () => ({
		prompt_injection: {
			enabled: false,
			instruction: '',
			position: 'SYSTEM' as const,
			activation: activation()
		},
		warning_modal: {
			enabled: false,
			title: $i18n.t('Important reminder'),
			message: $i18n.t('LLMs can make mistakes. Double-check important answers.'),
			confirmation_text: $i18n.t('Continue'),
			must_acknowledge: true,
			cadence: 'BEGINNING' as const,
			cadence_value: null,
			prompt_numbers: []
		},
		response_timing: {
			mode: 'NORMAL' as const,
			delay_seconds: null,
			show_loading: true,
			reveal_style: 'FULL' as const,
			target_duration_seconds: null,
			stream_unit: 'CHARACTER' as const,
			minimum_chunk_size: 1,
			maximum_chunk_size: 20,
			punctuation_pauses: false,
			rate_value: null,
			rate_unit: 'CHARACTERS_PER_SECOND' as const
		}
	});

	const duplicate = (condition: ExperimentCondition) => {
		const copy = structuredClone(condition);
		delete copy.id;
		copy.is_control = false;
		copy.name = `${condition.name} ${$i18n.t('copy')}`;
		copy.allocation_percent = 0;
		conditions = [...conditions, copy];
	};
	const addCondition = () => {
		conditions = [
			...conditions,
			{
				name: $i18n.t('Treatment condition'),
				allocation_percent: 0,
				enabled: true,
				is_control: false,
				...normalSettings()
			}
		];
	};
	const reset = (condition: ExperimentCondition) => {
		Object.assign(condition, normalSettings());
		conditions = [...conditions];
	};
	const remove = (condition: ExperimentCondition) => {
		if (!condition.is_control)
			conditions = conditions.filter((candidate) => candidate !== condition);
	};
	const summary = (condition: ExperimentCondition) => {
		if (condition.is_control) return $i18n.t('Normal, non-perturbed behavior.');
		const parts = [];
		if (condition.prompt_injection.enabled) parts.push($i18n.t('Prompt injection enabled'));
		if (condition.warning_modal.enabled) parts.push($i18n.t('Reliability warning enabled'));
		if (condition.response_timing.mode !== 'NORMAL')
			parts.push(`${$i18n.t('Response timing')}: ${condition.response_timing.mode.toLowerCase()}`);
		return parts.length ? `${parts.join('; ')}.` : $i18n.t('Normal, non-perturbed behavior.');
	};
	const sampleResponse = $i18n.t(
		'This is a sample assistant response. It demonstrates how each sentence, pause, and chunk will appear to participants.'
	);
	const stopClock = () => {
		if (timingClock !== null) clearInterval(timingClock);
		timingClock = null;
	};
	const startClock = (startedAt: number, totalMs: number, run: number) => {
		stopClock();
		timingPreviewTotalMs = totalMs;
		timingPreviewElapsedMs = 0;
		timingClock = setInterval(() => {
			if (run !== timingPreviewRun) return;
			timingPreviewElapsedMs = Math.min(totalMs, performance.now() - startedAt);
		}, 50);
	};
	const waitUntil = (deadline: number, run: number) =>
		waitUntilPreviewDeadline(deadline, () => run === timingPreviewRun);
	const cancelTimingPreview = (condition?: ExperimentCondition) => {
		if (condition && timingPreviewCondition !== condition) return;
		timingPreviewRun += 1;
		stopClock();
		if (timingPreviewState === 'WAITING' || timingPreviewState === 'STREAMING') {
			timingPreviewState = 'CANCELLED';
		}
	};
	const previewSeconds = (milliseconds: number) => (milliseconds / 1000).toFixed(1);
	const remainingPreviewSeconds = () =>
		previewSeconds(Math.max(0, timingPreviewTotalMs - timingPreviewElapsedMs));
	const previewTiming = async (condition: ExperimentCondition) => {
		const run = ++timingPreviewRun;
		stopClock();
		timingPreviewCondition = condition;
		timingPreviewText = '';
		const timing = condition.response_timing;
		const startedAt = performance.now();
		const finish = () => {
			if (run !== timingPreviewRun) return;
			timingPreviewElapsedMs = timingPreviewTotalMs;
			timingPreviewState = 'COMPLETE';
			stopClock();
		};

		if (timing.mode === 'NORMAL') {
			timingPreviewText = sampleResponse;
			timingPreviewState = 'COMPLETE';
			timingPreviewElapsedMs = 0;
			timingPreviewTotalMs = 0;
			return;
		}

		if (timing.mode === 'DELAYED') {
			const delayMs = (timing.delay_seconds ?? 0) * 1000;
			const chunks =
				timing.reveal_style === 'QUICK_STREAM'
					? deterministicPreviewChunks(
							sampleResponse,
							'CHARACTER',
							timing.maximum_chunk_size,
							timing.maximum_chunk_size,
							run
						)
					: [sampleResponse];
			const streamMs =
				timing.reveal_style === 'QUICK_STREAM'
					? chunks.slice(1).reduce((total, chunk) => total + (chunk.length / 500) * 1000, 0)
					: 0;
			timingPreviewState = 'WAITING';
			startClock(startedAt, delayMs + streamMs, run);
			if (!(await waitUntil(startedAt + delayMs, run))) return;
			if (timing.reveal_style === 'FULL') {
				timingPreviewText = sampleResponse;
				finish();
				return;
			}
			timingPreviewState = 'STREAMING';
			for (const [index, chunk] of chunks.entries()) {
				if (index && !(await waitUntil(performance.now() + (chunk.length / 500) * 1000, run)))
					return;
				timingPreviewText += chunk;
			}
			finish();
			return;
		}

		const slow = timing.mode === 'SLOW';
		const targetDuration = slow || timing.rate_unit === 'TARGET_DURATION';
		const chunks = deterministicPreviewChunks(
			sampleResponse,
			slow ? timing.stream_unit : timing.rate_unit === 'WORDS_PER_SECOND' ? 'WORD' : 'CHARACTER',
			slow ? timing.minimum_chunk_size : timing.maximum_chunk_size,
			timing.maximum_chunk_size,
			run
		);
		const weights = chunks.slice(1).map((chunk) => {
			const amount =
				timing.rate_unit === 'WORDS_PER_SECOND' && !slow
					? Math.max(1, chunk.trim().split(/\s+/).length)
					: Array.from(chunk).length;
			return slow && timing.punctuation_pauses && /[.!?,;:]\s*$/.test(chunk)
				? amount * 1.35
				: amount;
		});
		const totalMs = targetDuration
			? (timing.target_duration_seconds ?? 0) * 1000
			: weights.reduce((total, amount) => total + (amount / (timing.rate_value ?? 1)) * 1000, 0);
		timingPreviewState = 'STREAMING';
		startClock(startedAt, totalMs, run);
		if (chunks.length) timingPreviewText = chunks[0];
		let cumulativeWeight = 0;
		const totalWeight = weights.reduce((total, weight) => total + weight, 0);
		for (let index = 1; index < chunks.length; index += 1) {
			cumulativeWeight += weights[index - 1] ?? 0;
			const deadline = targetDuration
				? startedAt + totalMs * (cumulativeWeight / Math.max(totalWeight, 1))
				: startedAt +
					weights
						.slice(0, index)
						.reduce((total, amount) => total + (amount / (timing.rate_value ?? 1)) * 1000, 0);
			if (!(await waitUntil(deadline, run))) return;
			timingPreviewText += chunks[index];
		}
		finish();
	};
	onDestroy(() => {
		cancelTimingPreview();
	});
</script>

<section class="space-y-4 rounded-2xl border border-gray-100 p-4 dark:border-gray-850">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div>
			<h3 class="font-medium">{$i18n.t('LLM behavior and response perturbations')}</h3>
			<p class="mt-1 text-xs text-gray-500">
				{$i18n.t(
					'Conditions are assigned once per participant. Set one condition to 100% and all others, including control, to 0% to give every new participant in the group the same condition. Existing sessions keep their assigned condition.'
				)}
			</p>
			<p class="mt-1 text-xs text-gray-500">
				{$i18n.t(
					'Behaviors within a condition work together. For example, enable the reliability-warning modal and delayed responses in the same condition: warnings follow their configured cadence and delays apply to every response. Hidden prompt instructions are applied only on the server.'
				)}
			</p>
		</div>
		<button class="plan-button" type="button" on:click={addCondition}
			>+ {$i18n.t('Condition')}</button
		>
	</div>

	{#each conditions as condition}
		<article class="rounded-xl bg-gray-50 p-4 dark:bg-gray-900">
			<div class="grid gap-3 md:grid-cols-[1fr_10rem_8rem_auto]">
				<label class="experiment-label">
					{$i18n.t('Condition name')}
					<input
						class="experiment-input mt-1"
						bind:value={condition.name}
						disabled={condition.is_control}
					/>
				</label>
				<label class="experiment-label">
					{$i18n.t('Allocation percent')}
					<input
						class="experiment-input mt-1"
						type="number"
						min="0"
						max="100"
						step="1"
						bind:value={condition.allocation_percent}
					/>
				</label>
				<label
					class="flex items-center gap-2 self-end rounded-xl bg-white px-3 py-2 text-xs font-medium dark:bg-gray-850"
				>
					<input type="checkbox" bind:checked={condition.enabled} disabled={condition.is_control} />
					{$i18n.t('Enabled')}
				</label>
				<div class="flex flex-wrap items-end gap-2">
					<button class="plan-button" type="button" on:click={() => duplicate(condition)}
						>{$i18n.t('Duplicate')}</button
					>
					{#if !condition.is_control}
						<button class="plan-button" type="button" on:click={() => reset(condition)}
							>{$i18n.t('Restore normal')}</button
						>
						<button class="plan-button" type="button" on:click={() => remove(condition)}
							>{$i18n.t('Remove')}</button
						>
					{/if}
				</div>
			</div>
			<p class="mt-2 text-xs text-gray-500">{summary(condition)}</p>

			{#if !condition.is_control}
				<div class="mt-4 grid gap-4 xl:grid-cols-2">
					<div class="rounded-xl border border-gray-200 p-3 dark:border-gray-800">
						<label class="flex items-center gap-2 text-sm font-medium"
							><input type="checkbox" bind:checked={condition.prompt_injection.enabled} />
							{$i18n.t('Prompt injection')}</label
						>
						{#if condition.prompt_injection.enabled}
							<label class="experiment-label mt-3">
								{$i18n.t('Additional hidden instruction')}
								<textarea
									class="experiment-input min-h-24"
									bind:value={condition.prompt_injection.instruction}
								></textarea>
							</label>
							<div class="mt-3 grid gap-2 sm:grid-cols-1">
								<label class="experiment-label"
									>{$i18n.t('Insertion position')}<select
										class="experiment-input mt-1"
										bind:value={condition.prompt_injection.position}
										><option value="SYSTEM">{$i18n.t('Additional system instruction')}</option
										><option value="BEFORE_PARTICIPANT"
											>{$i18n.t('Before participant message')}</option
										><option value="AFTER_PARTICIPANT"
											>{$i18n.t('After participant message')}</option
										></select
									></label
								>
							</div>
							<ExperimentActivationControls
								bind:rule={condition.prompt_injection.activation}
								{items}
							/>
							<p class="mt-2 text-xs text-amber-700 dark:text-amber-300">
								{$i18n.t(
									'System instructions generally have higher priority; before/after positions are request-only user-role messages.'
								)}
							</p>
						{/if}
					</div>

					<div class="rounded-xl border border-gray-200 p-3 dark:border-gray-800">
						<label class="flex items-center gap-2 text-sm font-medium"
							><input type="checkbox" bind:checked={condition.warning_modal.enabled} />
							{$i18n.t('Reliability-warning modal')}</label
						>
						{#if condition.warning_modal.enabled}
							<div class="mt-3 grid gap-3">
								<label class="experiment-label"
									>{$i18n.t('Modal title')}<input
										class="experiment-input"
										bind:value={condition.warning_modal.title}
									/></label
								><label class="experiment-label"
									>{$i18n.t('Modal message')}<textarea
										class="experiment-input min-h-20"
										bind:value={condition.warning_modal.message}
									></textarea></label
								>
							</div>
							<div class="mt-3 grid gap-3 sm:grid-cols-2">
								<label class="experiment-label"
									>{$i18n.t('Confirmation text')}<input
										class="experiment-input"
										bind:value={condition.warning_modal.confirmation_text}
									/></label
								><label class="experiment-label"
									>{$i18n.t('Cadence')}<select
										class="experiment-input"
										bind:value={condition.warning_modal.cadence}
										><option value="BEGINNING">{$i18n.t('Once at beginning')}</option><option
											value="EVERY_N_PROMPTS">{$i18n.t('Every N prompts')}</option
										><option value="PROMPT_LIST">{$i18n.t('Prompt-number list')}</option><option
											value="ONCE_AFTER_PROMPT">{$i18n.t('Once after prompt')}</option
										><option value="EVERY_N_ACTIVE_MINUTES"
											>{$i18n.t('Every N active minutes')}</option
										><option value="AFTER_ACTIVE_MINUTES">{$i18n.t('After active minutes')}</option
										><option value="ONCE_AFTER_ACTIVE_DELAY"
											>{$i18n.t('Once after active-time delay')}</option
										></select
									></label
								>
							</div>
							{#if condition.warning_modal.cadence === 'PROMPT_LIST'}<label
									class="experiment-label mt-3"
									>{$i18n.t('Prompt numbers (comma-separated)')}<input
										class="experiment-input mt-1"
										value={condition.warning_modal.prompt_numbers.join(', ')}
										on:change={(event) => {
											condition.warning_modal.prompt_numbers = event.currentTarget.value
												.split(',')
												.map((value) => Number(value.trim()))
												.filter((value) => Number.isInteger(value) && value > 0);
											conditions = [...conditions];
										}}
									/></label
								>{:else if condition.warning_modal.cadence !== 'BEGINNING'}<label
									class="experiment-label mt-3"
									>{$i18n.t('Cadence value')}<input
										class="experiment-input mt-1"
										type="number"
										min="1"
										bind:value={condition.warning_modal.cadence_value}
									/></label
								>{/if}
							<label class="mt-2 flex items-center gap-2 text-xs"
								><input type="checkbox" bind:checked={condition.warning_modal.must_acknowledge} />
								{$i18n.t('Require acknowledgement')}</label
							>
							<button class="plan-button mt-2" type="button" on:click={() => (preview = condition)}
								>{$i18n.t('Preview')}</button
							>
						{/if}
					</div>

					<div
						class="rounded-xl border border-gray-200 p-3 dark:border-gray-800"
						on:input={() => cancelTimingPreview(condition)}
						on:change={() => cancelTimingPreview(condition)}
					>
						<label class="experiment-label !text-sm !text-gray-900 dark:!text-gray-100"
							>{$i18n.t('Response timing')}<select
								class="experiment-input mt-2"
								bind:value={condition.response_timing.mode}
								><option value="NORMAL">{$i18n.t('Normal')}</option><option value="DELAYED"
									>{$i18n.t('Delayed reveal')}</option
								><option value="SLOW">{$i18n.t('Controlled slow streaming')}</option><option
									value="FAST">{$i18n.t('Fast synthetic reveal')}</option
								></select
							></label
						>
						{#if condition.response_timing.mode === 'DELAYED'}<label class="experiment-label mt-3"
								>{$i18n.t('Delay in seconds')}<input
									class="experiment-input mt-1"
									type="number"
									min="0.1"
									max="300"
									step="0.1"
									bind:value={condition.response_timing.delay_seconds}
								/></label
							>
							<div class="mt-3 grid grid-cols-2 gap-2">
								<label class="experiment-label"
									>{$i18n.t('Reveal style')}<select
										class="experiment-input"
										bind:value={condition.response_timing.reveal_style}
										><option value="FULL">{$i18n.t('Full response at once')}</option><option
											value="QUICK_STREAM">{$i18n.t('Quick stream after delay')}</option
										></select
									></label
								><label class="flex items-center gap-2 self-end pb-3 text-xs"
									><input type="checkbox" bind:checked={condition.response_timing.show_loading} />
									{$i18n.t('Show loading indicator')}</label
								>
							</div>{/if}
						{#if condition.response_timing.mode === 'SLOW' || (condition.response_timing.mode === 'FAST' && condition.response_timing.rate_unit === 'TARGET_DURATION')}<label
								class="experiment-label mt-3"
								>{$i18n.t('Target display duration in seconds')}<input
									class="experiment-input mt-1"
									type="number"
									min="1"
									max="600"
									bind:value={condition.response_timing.target_duration_seconds}
								/></label
							>{/if}
						{#if condition.response_timing.mode === 'SLOW'}<div class="mt-3 grid grid-cols-3 gap-2">
								<label class="experiment-label"
									>{$i18n.t('Stream unit')}<select
										class="experiment-input"
										bind:value={condition.response_timing.stream_unit}
										><option value="CHARACTER">{$i18n.t('Characters')}</option><option value="WORD"
											>{$i18n.t('Words')}</option
										><option value="CHUNK">{$i18n.t('Chunks')}</option></select
									></label
								><label class="experiment-label"
									>{$i18n.t('Minimum chunk size')}<input
										class="experiment-input"
										type="number"
										min="1"
										max="1000"
										bind:value={condition.response_timing.minimum_chunk_size}
									/></label
								><label class="experiment-label"
									>{$i18n.t('Maximum chunk size')}<input
										class="experiment-input"
										type="number"
										min="1"
										max="1000"
										bind:value={condition.response_timing.maximum_chunk_size}
									/></label
								>
							</div>
							<label class="mt-2 flex items-center gap-2 text-xs"
								><input
									type="checkbox"
									bind:checked={condition.response_timing.punctuation_pauses}
								/>
								{$i18n.t('Add natural punctuation pauses')}</label
							>{/if}
						{#if condition.response_timing.mode === 'FAST'}<div class="mt-3 grid grid-cols-2 gap-2">
								<label class="experiment-label"
									>{$i18n.t('Rate mode')}<select
										class="experiment-input"
										bind:value={condition.response_timing.rate_unit}
										><option value="CHARACTERS_PER_SECOND"
											>{$i18n.t('Characters per second')}</option
										><option value="WORDS_PER_SECOND">{$i18n.t('Words per second')}</option><option
											value="TARGET_DURATION">{$i18n.t('Target duration')}</option
										></select
									></label
								>{#if condition.response_timing.rate_unit !== 'TARGET_DURATION'}<label
										class="experiment-label"
										>{$i18n.t('Rate value')}<input
											class="experiment-input"
											type="number"
											min="0.1"
											max="5000"
											bind:value={condition.response_timing.rate_value}
										/></label
									>{/if}
							</div>{/if}
						<div class="mt-3 flex flex-wrap items-center gap-2">
							<button class="plan-button" type="button" on:click={() => previewTiming(condition)}
								>{timingPreviewCondition === condition && timingPreviewState !== 'IDLE'
									? $i18n.t('Restart preview')
									: $i18n.t('Preview timing')}</button
							>
							{#if timingPreviewCondition === condition && (timingPreviewState === 'WAITING' || timingPreviewState === 'STREAMING')}
								<button
									class="plan-button"
									type="button"
									on:click={() => cancelTimingPreview(condition)}>{$i18n.t('Cancel')}</button
								>
							{/if}
						</div>
						{#if timingPreviewCondition === condition}
							<div class="mt-2 flex items-center justify-between text-[11px] text-gray-500">
								<span>{$i18n.t(timingPreviewState)}</span>
								<span>
									{$i18n.t('Elapsed')}: {previewSeconds(timingPreviewElapsedMs)}s
									{#if timingPreviewTotalMs > 0}· {$i18n.t('Remaining')}: {remainingPreviewSeconds()}s{/if}
								</span>
							</div>
							<div
								class="mt-1 min-h-14 rounded-xl border border-gray-200 bg-white p-3 text-sm dark:border-gray-700 dark:bg-gray-850"
								aria-live="polite"
							>
								{#if timingPreviewText}
									{timingPreviewText}
								{:else if timingPreviewState === 'WAITING' && condition.response_timing.show_loading}
									<span class="animate-pulse text-gray-500"
										>{$i18n.t('Waiting for the response…')}</span
									>
								{/if}
							</div>
						{/if}
					</div>
				</div>
			{/if}
		</article>
	{/each}
	<p class="text-xs text-gray-500">
		{$i18n.t('Enabled allocations must total 100%. A normal control condition is always required.')}
	</p>
</section>

{#if preview}
	<div
		class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/40 p-4"
		role="presentation"
		on:mousedown={(event) => {
			if (event.currentTarget === event.target) preview = null;
		}}
	>
		<div
			class="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl dark:bg-gray-900"
			role="dialog"
			aria-modal="true"
			aria-label={$i18n.t('Warning preview')}
		>
			<h2 class="text-lg font-semibold">{preview.warning_modal.title}</h2>
			<p class="mt-3 whitespace-pre-wrap text-sm">{preview.warning_modal.message}</p>
			<div class="mt-6 flex justify-end">
				<button
					class="rounded-full bg-black px-5 py-2 text-sm text-white dark:bg-white dark:text-black"
					on:click={() => (preview = null)}>{preview.warning_modal.confirmation_text}</button
				>
			</div>
		</div>
	</div>
{/if}
