<script lang="ts">
	import MetricCards from './MetricCards.svelte';
	export let data: any;
	export let onOpen: (id: string) => void;
	const labels = {
		total_prompts: 'Total prompts',
		total_responses: 'Assistant responses',
		total_tokens: 'Total tokens',
		average_prompts_per_completed_run: 'Prompts per completed run',
		average_tokens_per_completed_run: 'Tokens per completed run'
	};
	$: metrics = Object.fromEntries(Object.keys(labels).map((key) => [key, data?.metrics?.[key]]));
</script>

<MetricCards {metrics} {labels} />
<p class="my-4 text-xs text-gray-500">
	{data?.unattributed?.prompts ?? 0} prompts without a step assignment · {data?.legacy_fallback_sessions ??
		0} historical runs use time-window attribution
</p>
{#if data?.request_timing}<p class="mb-4 text-sm">
		{data.request_timing.requests} request records · {data.request_timing.delayed_responses} delayed responses{#if data.request_timing.average_artificial_delay_seconds != null}
			· {data.request_timing.average_artificial_delay_seconds.toFixed(2)} sec average artificial delay{/if}
	</p>{/if}
<div class="grid gap-4 xl:grid-cols-2">
	{#each ['workflow', 'group', 'condition', 'step'] as dimension}
		<section class="min-w-0 rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
			<h2 class="mb-3 font-semibold capitalize">By {dimension}</h2>
			<div class="overflow-x-auto">
				<table class="w-full text-left text-sm">
					<thead><tr><th>Name</th><th>Prompts</th><th>Tokens</th></tr></thead><tbody>
						{#each data?.[`by_${dimension}`] ?? [] as row}<tr
								><td class="py-2 pr-3"
									>{row.label}{#if dimension === 'step'}<div class="text-xs text-gray-500">
											{row.configuration_id?.slice(0, 10)}
										</div>{/if}</td
								><td>{row.prompts}</td><td>{row.total_tokens ?? row.tokens}</td></tr
							>{:else}<tr><td colspan="3" class="py-4 text-gray-500">No usage.</td></tr>{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/each}
</div>
<section class="mt-4 rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
	<h2 class="font-semibold">Participants with the most token usage</h2>
	{#each data?.top_participants ?? [] as row}<button
			class="mt-3 block text-left text-sm underline"
			on:click={() => onOpen(row.session_id)}
			>{row.name ?? row.participant_id} · {row.prompts} prompts · {row.total_tokens} tokens</button
		>{/each}
</section>
