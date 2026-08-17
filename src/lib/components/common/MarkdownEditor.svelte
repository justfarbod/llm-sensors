<script lang="ts">
	import { getContext, tick } from 'svelte';
	import type { i18n as i18nType } from 'i18next';
	import type { Writable } from 'svelte/store';

	import Bold from '$lib/components/icons/Bold.svelte';
	import H2 from '$lib/components/icons/H2.svelte';
	import Italic from '$lib/components/icons/Italic.svelte';
	import Link from '$lib/components/icons/Link.svelte';
	import ListBullet from '$lib/components/icons/ListBullet.svelte';
	import NumberedList from '$lib/components/icons/NumberedList.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { applyMarkdownFormat, type MarkdownFormat } from '$lib/utils/markdownEditor';
	import SafeMarkdown from './SafeMarkdown.svelte';

	const i18n: Writable<i18nType> = getContext('i18n');

	export let value = '';
	export let placeholder = '';
	export let ariaLabel = '';
	export let readonly = false;
	export let required = false;
	export let className = '';
	export let textareaClass = '';
	export let previewClass = 'markdown-prose-sm';
	export let experimentField: 'essay' | 'question' | undefined = undefined;
	export let sessionTaskId: string | undefined = undefined;
	export let onInput: (event: Event) => void = () => {};

	let textarea: HTMLTextAreaElement;
	let preview = false;

	const formatLabels: Record<MarkdownFormat, string> = {
		bold: 'Bold',
		italic: 'Italic',
		heading: 'Heading',
		'bullet-list': 'Bullet List',
		'numbered-list': 'Ordered List',
		link: 'Link'
	};
	const formats: MarkdownFormat[] = [
		'bold',
		'italic',
		'heading',
		'bullet-list',
		'numbered-list',
		'link'
	];

	const applyFormat = async (format: MarkdownFormat) => {
		if (readonly || preview || !textarea) return;
		const edit = applyMarkdownFormat(
			value,
			textarea.selectionStart ?? value.length,
			textarea.selectionEnd ?? value.length,
			format
		);
		value = edit.value;
		await tick();
		textarea.focus();
		textarea.setSelectionRange(edit.selectionStart, edit.selectionEnd);
		textarea.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
	};
</script>

<div
	class="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-gray-300 bg-white focus-within:border-gray-500 focus-within:ring-3 focus-within:ring-gray-400/20 dark:border-gray-600 dark:bg-gray-900 dark:focus-within:border-gray-400 {className}"
>
	<textarea
		bind:this={textarea}
		bind:value
		class:hidden={preview}
		class="min-h-0 min-w-0 flex-1 resize-none bg-transparent p-3 text-sm leading-6 text-gray-900 outline-hidden placeholder:text-gray-400 dark:text-gray-100 {textareaClass}"
		{placeholder}
		aria-label={ariaLabel || placeholder || $i18n.t('Markdown text')}
		{readonly}
		{required}
		data-experiment-field={experimentField}
		data-session-task-id={sessionTaskId}
		on:input={(event) => onInput(event)}
	></textarea>
	{#if preview}
		<div class="min-h-0 min-w-0 flex-1 overflow-y-auto p-3 text-gray-900 dark:text-gray-100">
			{#if value.trim()}
				<SafeMarkdown content={value} className={previewClass} />
			{:else}
				<p class="text-sm text-gray-400">{placeholder}</p>
			{/if}
		</div>
	{/if}
	<div
		class="flex min-h-10 shrink-0 items-center gap-0.5 overflow-x-auto border-t border-gray-200 bg-gray-50 p-1 dark:border-gray-700 dark:bg-gray-850"
		aria-label={$i18n.t('Markdown formatting')}
	>
		<span class="shrink-0 px-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
			{$i18n.t('Markdown')}
		</span>
		{#each formats as format}
			<Tooltip content={$i18n.t(formatLabels[format])}>
				<button
					type="button"
					class="rounded-md p-1.5 text-gray-600 transition hover:bg-gray-200 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-35 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-white"
					disabled={readonly || preview}
					aria-label={$i18n.t(formatLabels[format])}
					on:click={() => applyFormat(format)}
				>
					{#if format === 'bold'}
						<Bold className="size-4" />
					{:else if format === 'italic'}
						<Italic className="size-4" />
					{:else if format === 'heading'}
						<H2 className="size-4" />
					{:else if format === 'bullet-list'}
						<ListBullet className="size-4" />
					{:else if format === 'numbered-list'}
						<NumberedList className="size-4" />
					{:else}
						<Link className="size-4" />
					{/if}
				</button>
			</Tooltip>
		{/each}
		<div class="ml-auto flex shrink-0 rounded-lg bg-gray-200 p-0.5 text-xs dark:bg-gray-700">
			<button
				type="button"
				class="rounded-md px-2 py-1 {preview ? '' : 'bg-white shadow-sm dark:bg-gray-850'}"
				aria-pressed={!preview}
				on:click={() => (preview = false)}>{$i18n.t('Write')}</button
			>
			<button
				type="button"
				class="rounded-md px-2 py-1 {preview ? 'bg-white shadow-sm dark:bg-gray-850' : ''}"
				aria-pressed={preview}
				on:click={() => (preview = true)}>{$i18n.t('Preview')}</button
			>
		</div>
	</div>
</div>
