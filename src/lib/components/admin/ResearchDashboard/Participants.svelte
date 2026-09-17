<script lang="ts">
	import Pagination from './Pagination.svelte';
	export let data: any;
	export let selected: Set<string>;
	export let page: number;
	export let limit: number;
	export let onPage: (page: number) => void;
	export let onOpen: (id: string) => void;
	export let onToggle: (id: string) => void;
	export let onSort: (key: string) => void;
	export let fmtDuration: (n: number) => string;
	export let fmtDate: (n: any) => string;
</script>

<div class="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800">
	<table class="w-full text-left text-sm">
		<thead class="bg-gray-50 dark:bg-gray-850"
			><tr
				><th>Select</th><th><button on:click={() => onSort('name')}>Participant</button></th><th
					>Workflow / configuration</th
				><th>Group / condition</th><th>Status / progress</th><th
					><button on:click={() => onSort('session_start_time')}>Start / end time</button></th
				><th
					><button on:click={() => onSort('session_duration')}>Duration</button></th
				><th><button on:click={() => onSort('prompts')}>Whole-run AI</button></th></tr
			></thead
		>
		<tbody
			>{#each data?.items ?? [] as row}<tr class="border-t border-gray-100 dark:border-gray-800">
					<td
						><input
							aria-label={`Select ${row.name}`}
							type="checkbox"
							disabled={!row.session_id}
							checked={selected.has(row.session_id)}
							on:change={() => onToggle(row.session_id)}
						/></td
					>
					<td
						><button
							class="text-left font-medium underline decoration-gray-300 underline-offset-4"
							disabled={!row.session_id}
							on:click={() => onOpen(row.session_id)}>{row.name ?? row.participant_id}</button
						>
						<div class="mt-1 text-xs text-gray-500">{row.participant_id}</div>
						{#if row.is_demo}<span
								class="mt-1 inline-block rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900"
								>Synthetic demo</span
							>{/if}</td
					>
					<td class="min-w-56"
						>{row.workflow_name}
						<div class="mt-1 text-xs text-gray-500">
							Applied v{row.plan_version ?? '—'} · {row.configuration_id?.slice(0, 10) ?? 'Unknown'}
						</div></td
					>
					<td
						>{row.group_name}
						<div class="mt-1 text-xs text-gray-500">{row.condition_name ?? 'Unassigned'}</div></td
					>
					<td
						>{row.state}
						<div class="mt-1 whitespace-nowrap text-xs text-gray-500">
							{row.task_progress?.completed ?? 0} completed · {row.task_progress?.skipped ?? 0} skipped
							/ {row.task_progress?.total ?? 0}
						</div></td
					>
					<td class="whitespace-nowrap"
						>{fmtDate(row.session_start_time)}
						<div class="mt-1">– {fmtDate(row.session_completion_time)}</div></td
					><td class="whitespace-nowrap">{fmtDuration(row.session_duration)}</td><td
						>{row.prompts} prompts
						<div class="mt-1 text-xs text-gray-500">
							{row.responses} responses · {row.total_tokens} tokens
						</div></td
					>
				</tr>{:else}<tr
					><td colspan="8" class="py-12 text-center text-gray-500"
						>No participants match these filters.</td
					></tr
				>{/each}</tbody
		>
	</table>
</div>
<Pagination total={data?.total ?? 0} {page} {limit} {onPage} />

<style>
	th,
	td {
		padding: 0.85rem;
		vertical-align: top;
	}
	th {
		font-weight: 500;
		white-space: nowrap;
	}
</style>
