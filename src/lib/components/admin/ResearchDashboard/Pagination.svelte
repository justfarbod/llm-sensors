<script lang="ts">
	export let total = 0;
	export let page = 1;
	export let limit = 25;
	export let onPage: (page: number) => void = () => {};
	$: pages = Math.max(1, Math.ceil(total / limit));
</script>

<div class="mt-3 flex items-center justify-between text-xs text-gray-500">
	<span>Page {page} of {pages}</span>
	<div class="flex gap-2">
		<button class="research-button" disabled={page <= 1} on:click={() => onPage(page - 1)}
			>Previous</button
		>
		<button class="research-button" disabled={page >= pages} on:click={() => onPage(page + 1)}
			>Next</button
		>
	</div>
</div>
