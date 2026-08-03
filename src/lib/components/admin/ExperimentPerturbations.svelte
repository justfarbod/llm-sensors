<script lang="ts">
	import { getContext, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import type { ExperimentCondition, PlanItem } from '$lib/apis/experiment-plans';
	import ExperimentActivationControls from './ExperimentActivationControls.svelte';

	export let conditions: ExperimentCondition[] = [];
	export let items: PlanItem[] = [];
	const i18n: Writable<i18nType> = getContext('i18n');
	let preview: ExperimentCondition | null = null;
	let timingPreviewCondition: ExperimentCondition | null = null;
	let timingPreviewText = '';
	let timingPreviewRun = 0;

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
		memory_injection: {
			enabled: false,
			content: '',
			persist_for_session: false,
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
		if (condition.memory_injection.enabled) parts.push($i18n.t('Memory injection enabled'));
		if (condition.warning_modal.enabled) parts.push($i18n.t('Reliability warning enabled'));
		if (condition.response_timing.mode !== 'NORMAL')
			parts.push(`${$i18n.t('Response timing')}: ${condition.response_timing.mode.toLowerCase()}`);
		return parts.length ? `${parts.join('; ')}.` : $i18n.t('Normal, non-perturbed behavior.');
	};
	const wait = (milliseconds: number) =>
		new Promise((resolve) => setTimeout(resolve, milliseconds));
	const previewTiming = async (condition: ExperimentCondition) => {
		const run = ++timingPreviewRun;
		timingPreviewCondition = condition;
		timingPreviewText = '';
		const sample = $i18n.t('This is a sample assistant response.');
		const timing = condition.response_timing;
		if (timing.mode === 'DELAYED') {
			await wait(Math.min(1500, (timing.delay_seconds ?? 0) * 1000));
			if (run !== timingPreviewRun) return;
			if (timing.reveal_style === 'FULL') {
				timingPreviewText = sample;
				return;
			}
		}
		if (timing.mode === 'NORMAL') {
			timingPreviewText = sample;
			return;
		}
		const chunkSize = timing.mode === 'SLOW' ? 1 : 4;
		const totalDuration =
			timing.rate_unit === 'TARGET_DURATION' || timing.mode === 'SLOW'
				? Math.min(2000, (timing.target_duration_seconds ?? 1) * 1000)
				: 500;
		for (let index = 0; index < sample.length; index += chunkSize) {
			if (run !== timingPreviewRun) return;
			timingPreviewText += sample.slice(index, index + chunkSize);
			await wait(totalDuration / Math.ceil(sample.length / chunkSize));
		}
	};
	onDestroy(() => {
		timingPreviewRun += 1;
	});
</script>

<section class="space-y-4 rounded-2xl border border-gray-100 p-4 dark:border-gray-850">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div>
			<h3 class="font-medium">{$i18n.t('LLM behavior and response perturbations')}</h3>
			<p class="mt-1 text-xs text-gray-500">
				{$i18n.t(
					'Conditions are assigned once per participant. Hidden prompt and memory content is applied only on the server.'
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
				<label class="text-xs font-medium text-gray-500">
					{$i18n.t('Condition name')}
					<input
						class="plan-input mt-1"
						bind:value={condition.name}
						disabled={condition.is_control}
					/>
				</label>
				<label class="text-xs font-medium text-gray-500">
					{$i18n.t('Allocation percent')}
					<input
						class="plan-input mt-1"
						type="number"
						min="0"
						max="100"
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
							<textarea
								class="plan-input mt-3 min-h-24"
								bind:value={condition.prompt_injection.instruction}
								placeholder={$i18n.t('Additional hidden instruction')}
							></textarea>
							<div class="mt-3 grid gap-2 sm:grid-cols-1">
								<label class="text-xs"
									>{$i18n.t('Insertion position')}<select
										class="plan-input mt-1"
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
							><input type="checkbox" bind:checked={condition.memory_injection.enabled} />
							{$i18n.t('Memory or context injection')}</label
						>
						{#if condition.memory_injection.enabled}
							<textarea
								class="plan-input mt-3 min-h-24"
								bind:value={condition.memory_injection.content}
								placeholder={$i18n.t('Hidden experiment context')}
							></textarea>
							<label class="mt-2 flex items-center gap-2 text-xs"
								><input
									type="checkbox"
									bind:checked={condition.memory_injection.persist_for_session}
								/>
								{$i18n.t('Persist after first activation')}</label
							>
							<ExperimentActivationControls
								bind:rule={condition.memory_injection.activation}
								{items}
							/>
						{/if}
					</div>

					<div class="rounded-xl border border-gray-200 p-3 dark:border-gray-800">
						<label class="flex items-center gap-2 text-sm font-medium"
							><input type="checkbox" bind:checked={condition.warning_modal.enabled} />
							{$i18n.t('Reliability-warning modal')}</label
						>
						{#if condition.warning_modal.enabled}
							<input
								class="plan-input mt-3"
								bind:value={condition.warning_modal.title}
								aria-label={$i18n.t('Modal title')}
							/>
							<textarea
								class="plan-input mt-2 min-h-20"
								bind:value={condition.warning_modal.message}
								aria-label={$i18n.t('Modal message')}
							></textarea>
							<div class="mt-2 grid gap-2 sm:grid-cols-2">
								<input
									class="plan-input"
									bind:value={condition.warning_modal.confirmation_text}
									aria-label={$i18n.t('Confirmation text')}
								/><select class="plan-input" bind:value={condition.warning_modal.cadence}
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
								>
							</div>
							{#if condition.warning_modal.cadence === 'PROMPT_LIST'}<label
									class="mt-2 block text-xs"
									>{$i18n.t('Prompt numbers (comma-separated)')}<input
										class="plan-input mt-1"
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
									class="mt-2 block text-xs"
									>{$i18n.t('Cadence value')}<input
										class="plan-input mt-1"
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

					<div class="rounded-xl border border-gray-200 p-3 dark:border-gray-800">
						<label class="text-sm font-medium"
							>{$i18n.t('Response timing')}<select
								class="plan-input mt-2"
								bind:value={condition.response_timing.mode}
								><option value="NORMAL">{$i18n.t('Normal')}</option><option value="DELAYED"
									>{$i18n.t('Delayed reveal')}</option
								><option value="SLOW">{$i18n.t('Controlled slow streaming')}</option><option
									value="FAST">{$i18n.t('Fast synthetic reveal')}</option
								></select
							></label
						>
						{#if condition.response_timing.mode === 'DELAYED'}<label class="mt-2 block text-xs"
								>{$i18n.t('Delay in seconds')}<input
									class="plan-input mt-1"
									type="number"
									min="0.1"
									max="300"
									step="0.1"
									bind:value={condition.response_timing.delay_seconds}
								/></label
							>
							<div class="mt-2 grid grid-cols-2 gap-2">
								<select class="plan-input" bind:value={condition.response_timing.reveal_style}
									><option value="FULL">{$i18n.t('Full response at once')}</option><option
										value="QUICK_STREAM">{$i18n.t('Quick stream after delay')}</option
									></select
								><label class="flex items-center gap-2 text-xs"
									><input type="checkbox" bind:checked={condition.response_timing.show_loading} />
									{$i18n.t('Show loading indicator')}</label
								>
							</div>{/if}
						{#if condition.response_timing.mode === 'SLOW' || (condition.response_timing.mode === 'FAST' && condition.response_timing.rate_unit === 'TARGET_DURATION')}<label
								class="mt-2 block text-xs"
								>{$i18n.t('Target display duration in seconds')}<input
									class="plan-input mt-1"
									type="number"
									min="1"
									max="600"
									bind:value={condition.response_timing.target_duration_seconds}
								/></label
							>{/if}
						{#if condition.response_timing.mode === 'SLOW'}<div class="mt-2 grid grid-cols-3 gap-2">
								<select class="plan-input" bind:value={condition.response_timing.stream_unit}
									><option value="CHARACTER">{$i18n.t('Characters')}</option><option value="WORD"
										>{$i18n.t('Words')}</option
									><option value="CHUNK">{$i18n.t('Chunks')}</option></select
								><input
									class="plan-input"
									type="number"
									min="1"
									max="1000"
									bind:value={condition.response_timing.minimum_chunk_size}
									aria-label={$i18n.t('Minimum chunk size')}
								/><input
									class="plan-input"
									type="number"
									min="1"
									max="1000"
									bind:value={condition.response_timing.maximum_chunk_size}
									aria-label={$i18n.t('Maximum chunk size')}
								/>
							</div>
							<label class="mt-2 flex items-center gap-2 text-xs"
								><input
									type="checkbox"
									bind:checked={condition.response_timing.punctuation_pauses}
								/>
								{$i18n.t('Add natural punctuation pauses')}</label
							>{/if}
						{#if condition.response_timing.mode === 'FAST'}<div class="mt-2 grid grid-cols-2 gap-2">
								<select class="plan-input" bind:value={condition.response_timing.rate_unit}
									><option value="CHARACTERS_PER_SECOND">{$i18n.t('Characters per second')}</option
									><option value="WORDS_PER_SECOND">{$i18n.t('Words per second')}</option><option
										value="TARGET_DURATION">{$i18n.t('Target duration')}</option
									></select
								>{#if condition.response_timing.rate_unit !== 'TARGET_DURATION'}<input
										class="plan-input"
										type="number"
										min="0.1"
										max="5000"
										bind:value={condition.response_timing.rate_value}
									/>{/if}
							</div>{/if}
						<button class="plan-button mt-3" type="button" on:click={() => previewTiming(condition)}
							>{$i18n.t('Preview timing')}</button
						>
						{#if timingPreviewCondition === condition}
							<div
								class="mt-2 min-h-14 rounded-xl bg-white p-3 text-sm dark:bg-gray-850"
								aria-live="polite"
							>
								{timingPreviewText || $i18n.t('Waiting for the response…')}
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
