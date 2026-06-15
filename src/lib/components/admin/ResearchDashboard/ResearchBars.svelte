<script lang="ts">
	export let title = '';
	export let items: { label: string; value?: number; count?: number; percentage?: number }[] = [];
	export let valueKey: 'value' | 'count' | 'percentage' = 'value';

	$: maximum = Math.max(1, ...items.map((item) => Number(item[valueKey] ?? 0)));
</script>

<section
	class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-850 dark:bg-gray-900"
>
	<h3 class="mb-4 text-sm font-semibold text-gray-900 dark:text-white">{title}</h3>
	{#if items.length}
		<div class="space-y-3">
			{#each items as item}
				<div>
					<div class="mb-1 flex justify-between gap-3 text-xs text-gray-500 dark:text-gray-400">
						<span class="truncate">{item.label || 'Not available'}</span>
						<span>{Number(item[valueKey] ?? 0).toLocaleString()}</span>
					</div>
					<div class="h-2 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
						<div
							class="h-full rounded-full bg-blue-500 transition-all"
							style={`width: ${Math.max(2, (Number(item[valueKey] ?? 0) / maximum) * 100)}%`}
						></div>
					</div>
				</div>
			{/each}
		</div>
	{:else}
		<p class="py-8 text-center text-sm text-gray-400">No experiment data yet.</p>
	{/if}
</section>
