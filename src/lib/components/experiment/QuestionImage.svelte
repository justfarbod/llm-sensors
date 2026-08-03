<script lang="ts">
	import { getContext, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { questionImageUrl } from '$lib/apis/question-tasks';
	const i18n: Writable<i18nType> = getContext('i18n');

	export let fileId: string;
	export let alt = '';
	export let className = '';
	let objectUrl = '';
	let current = '';
	let loading = false;
	let failed = false;
	let controller: AbortController | null = null;

	$: if (fileId && fileId !== current) {
		current = fileId;
		controller?.abort();
		controller = new AbortController();
		if (objectUrl) URL.revokeObjectURL(objectUrl);
		objectUrl = '';
		loading = true;
		failed = false;
		const requestedId = fileId;
		fetch(questionImageUrl(fileId), {
			headers: { authorization: `Bearer ${localStorage.token}` },
			signal: controller.signal
		})
			.then((response) => {
				if (!response.ok) throw new Error('Image unavailable');
				return response.blob();
			})
			.then((blob) => {
				if (current === requestedId) objectUrl = URL.createObjectURL(blob);
			})
			.catch((error) => {
				if (error?.name !== 'AbortError' && current === requestedId) failed = true;
			})
			.finally(() => {
				if (current === requestedId) loading = false;
			});
	}

	onDestroy(() => {
		controller?.abort();
		if (objectUrl) URL.revokeObjectURL(objectUrl);
	});
</script>

{#if objectUrl}
	<img src={objectUrl} {alt} class={className} draggable="false" />
{:else if loading}
	<div
		class="flex min-h-24 items-center justify-center rounded-xl bg-gray-100 text-xs text-gray-500 dark:bg-gray-800"
		aria-busy="true"
	>
		{$i18n.t('Loading image...')}
	</div>
{:else if failed}
	<div class="rounded-xl bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
		{$i18n.t('Question image could not be loaded.')}
	</div>
{/if}
