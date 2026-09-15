<script lang="ts">
	import MetricCards from './MetricCards.svelte';
	import ResearchBars from './ResearchBars.svelte';
	export let data: any;
	export let onWorkflow: (id: string) => void;
	const labels = {
		total_sessions: 'Started runs',
		active_sessions: 'Active runs',
		completed_sessions: 'Completed runs',
		completion_rate: 'Completion rate',
		average_session_duration: 'Average run duration'
	};
	$: metrics = Object.fromEntries(Object.keys(labels).map((key) => [key, data?.metrics?.[key]]));
</script>

<MetricCards {metrics} {labels} />
<p class="my-4 text-sm text-gray-500">
	Task progress: {data?.task_progress?.completed ?? 0} completed · {data?.task_progress?.skipped ??
		0} skipped / {data?.task_progress?.total ?? 0} assigned steps
</p>
<div class="grid gap-4 lg:grid-cols-2">
	{#each data?.workflows ?? [] as workflow}
		<button
			class="rounded-2xl border border-gray-200 p-5 text-left dark:border-gray-800"
			on:click={() => onWorkflow(workflow.id)}
		>
			<h2 class="font-semibold">{workflow.name}</h2>
			<p class="mt-2 text-sm text-gray-500">
				{workflow.runs} runs · {workflow.completed} completed · {workflow.active} active
			</p>
			<p class="mt-3 text-sm">View workflow steps →</p>
		</button>
	{/each}
	<ResearchBars title="Runs over time" items={data?.sessions_over_time ?? []} />
	<ResearchBars title="Run status" items={data?.states ?? []} />
</div>

{#each data?.workflow_steps ?? [] as workflow}
	<section class="mt-4 rounded-2xl border border-gray-200 p-5 dark:border-gray-800">
		<h2 class="font-semibold">{workflow.name}</h2>
		<p class="mt-1 text-xs text-gray-500">Configuration {workflow.configuration_id.slice(0, 10)}</p>
		<ol class="mt-3 space-y-2">
			{#each workflow.steps as step}<li class="flex flex-wrap justify-between gap-2 text-sm">
					<span>{step.position + 1}. {step.title}</span><span class="text-gray-500"
						>{step.completed} completed · {step.skipped} skipped · {step.pending} pending</span
					>
				</li>{/each}
		</ol>
	</section>
{/each}
