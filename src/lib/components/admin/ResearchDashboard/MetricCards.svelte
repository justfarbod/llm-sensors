<script lang="ts">
	export let metrics: Record<string, any> = {};
	export let labels: Record<string, string> = {};

	const display = (key: string, value: any) => {
		if (value === null || value === undefined) return 'Not available';
		if (key.includes('rate')) return `${value}%`;
		if (key.includes('duration') || key.includes('time_to') || key.includes('essay_to')) {
			return `${Math.round(value / 60)} min`;
		}
		return Number(value).toLocaleString();
	};
</script>

<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
	{#each Object.entries(metrics) as [key, value]}
		<div
			class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-850 dark:bg-gray-900"
		>
			<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{labels[key] ?? key}</div>
			<div class="mt-2 text-2xl font-semibold text-gray-900 dark:text-white">
				{display(key, value)}
			</div>
		</div>
	{/each}
</div>
