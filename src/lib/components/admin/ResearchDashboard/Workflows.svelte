<script lang="ts">
	import { onDestroy } from 'svelte';
	import {
		getResearchWorkflow,
		getWorkflowStepResults,
		type ResearchFilters,
		type WorkflowSummary,
		type WorkflowStepResults,
		type WorkflowDetail
	} from '$lib/apis/experiment-analytics';
	import ParticipantTasks from './ParticipantTasks.svelte';
	import Pagination from './Pagination.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	export let data: { items: WorkflowSummary[]; total: number };
	export let filters: ResearchFilters;
	export let onWorkflow: (id: string) => void;
	export let onConfiguration: (id: string) => void;
	export let onOpen: (id: string) => void;
	export let onOpenQuestion: (id: string) => void;
	export let fmtDate: (n: number | null) => string;
	let detail: WorkflowDetail | null = null;
	let result: WorkflowStepResults | null = null;
	let selectedStep = '';
	let resultPage = 1;
	let loading = false;
	let error = '';
	let controller: AbortController;
	let resultController: AbortController;
	$: signature = JSON.stringify(filters);
	$: loadWorkflow(signature);
	async function loadWorkflow(_signature: string) {
		controller?.abort();
		resultController?.abort();
		detail = null;
		result = null;
		selectedStep = '';
		error = '';
		if (!filters.workflow_id) {
			loading = false;
			return;
		}
		const current = new AbortController();
		controller = current;
		loading = true;
		try {
			detail = await getResearchWorkflow(
				localStorage.token,
				filters.workflow_id,
				filters,
				current.signal
			);
		} catch (e) {
			if (!current.signal.aborted) error = String(e);
		} finally {
			if (!current.signal.aborted) loading = false;
		}
	}
	async function loadStep(key: string, page = 1) {
		resultController?.abort();
		const current = new AbortController();
		resultController = current;
		selectedStep = key;
		resultPage = page;
		result = null;
		error = '';
		try {
			result = await getWorkflowStepResults(
				localStorage.token,
				filters.workflow_id!,
				key,
				{ ...filters, configuration_id: detail!.configuration_id, page, limit: 10 },
				current.signal
			);
		} catch (e) {
			if (!current.signal.aborted) error = String(e);
		}
	}
	onDestroy(() => {
		controller?.abort();
		resultController?.abort();
	});
</script>

{#if !filters.workflow_id}
	<div class="grid gap-4 lg:grid-cols-2">
		{#each data?.items ?? [] as workflow}
			<button
				class="rounded-2xl border border-gray-200 p-5 text-left dark:border-gray-800"
				on:click={() => onWorkflow(workflow.id)}
			>
				<h2 class="font-semibold">{workflow.name}</h2>
				<p class="mt-2 text-sm text-gray-500">
					{workflow.runs} runs · {workflow.completed} completed · {workflow.configurations.length} configurations
				</p>
				<p class="mt-2 text-xs text-gray-500">
					{workflow.groups.map((group) => group.name).join(' · ')}
				</p>
				<p class="mt-4 text-sm">Open workflow →</p>
			</button>{:else}<p class="py-12 text-gray-500">
				No deployed workflows match these filters.
			</p>{/each}
	</div>
{:else if loading}<div class="flex h-40 items-center justify-center"><Spinner /></div>
{:else if detail}
	<div class="mb-5">
		<h2 class="text-xl font-semibold">{detail.name}</h2>
		<p class="mt-2 text-sm text-gray-500">
			{detail.runs} runs · {detail.completed} completed · {detail.progression_mode
				.replaceAll('_', ' ')
				.toLowerCase()} · {detail.chat_mode.replaceAll('_', ' ').toLowerCase()}
		</p>
		<label class="mt-4 block text-sm"
			>Applied configuration
			<select
				aria-label="Applied configuration"
				class="ml-2 max-w-full rounded-lg bg-gray-100 p-2 dark:bg-gray-800"
				value={detail.configuration_id}
				on:change={(e) => onConfiguration(e.currentTarget.value)}
			>
				{#each detail.configurations as config}<option value={config.id}
						>{config.id.slice(0, 10)} · {config.source_revision == null
							? 'source revision unknown'
							: `source revision ${config.source_revision}`} · {config.runs} runs · {config.origin}</option
					>{/each}
			</select></label
		>
		<p class="mt-3 text-xs text-gray-500">
			Conditions: {detail.conditions.map((c) => `${c.name} (${c.runs})`).join(' · ')}
		</p>
	</div>
	<ol class="space-y-3">
		{#each detail.steps as step}<li
				class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800"
			>
				<div class="flex flex-wrap items-center justify-between gap-3">
					<div>
						<h3 class="font-semibold">{step.position + 1}. {step.title}</h3>
						<p class="mt-1 text-xs text-gray-500">
							{step.task_type} · {step.started} started · {step.completed} completed · {step.skipped}
							skipped · {step.pending} pending
						</p>
					</div>
					<button
						class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700"
						on:click={() => loadStep(step.key)}>View step results</button
					>
				</div>
				<p class="mt-3 text-sm">
					Average elapsed time: {step.average_elapsed_seconds == null
						? 'Unavailable'
						: `${Math.round(step.average_elapsed_seconds / 60)} min`} · {step.usage.prompts} prompts
				</p>
				{#if step.task_type === 'QUESTION'}<p class="mt-2 text-sm">
						Average graded score: {step.average_score ?? 'Pending'} / {step.maximum_score ?? '—'} · {step.pending_grades}
						pending grades
					</p>{/if}
				{#each step.essay_topics as topic}<p class="mt-2 text-sm">
						{topic.title} · {topic.submissions} essays · {topic.average_words} average words ({topic.minimum_words}–{topic.maximum_words})
					</p>{/each}
				{#if selectedStep === step.key}
					<div class="mt-4 border-t border-gray-100 pt-4 dark:border-gray-800">
						{#if result}
							{#each result.survey_distributions as question}<div class="mb-4 text-sm">
									<strong>{question.prompt}</strong>
									<p class="mt-1 text-gray-500">{question.answered} answered</p>
									{#each Object.entries(question.counts) as [label, count]}<p>
											{label}: {count}
										</p>{/each}
								</div>{/each}
							{#each result.items as row}<article
									class="mb-4 rounded-xl bg-gray-50 p-3 dark:bg-gray-850"
								>
									<button
										class="text-sm font-medium underline"
										on:click={() => onOpen(row.session_id)}>{row.name}</button
									>{#if row.is_demo}<span
											class="ml-2 rounded bg-amber-100 px-2 py-1 text-xs text-amber-900"
											>Synthetic demo</span
										>{/if}<ParticipantTasks tasks={[row]} {fmtDate} {onOpenQuestion} />
								</article>{:else}<p class="text-sm text-gray-500">
									No results match these filters.
								</p>{/each}
							<Pagination
								total={result.total}
								page={resultPage}
								limit={10}
								onPage={(page) => loadStep(step.key, page)}
							/>
						{:else if !error}<div class="flex h-20 items-center justify-center">
								<Spinner />
							</div>{/if}
					</div>{/if}
			</li>{/each}
	</ol>
{/if}
{#if error}<div role="alert" class="mt-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">
		{error}<button
			class="ml-3 underline"
			on:click={() => (selectedStep ? loadStep(selectedStep, resultPage) : loadWorkflow(signature))}
			>Retry</button
		>
	</div>{/if}
