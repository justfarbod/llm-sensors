<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import type { i18n as i18nType } from 'i18next';
	import type { Writable } from 'svelte/store';

	import { getEssayWorkspace, submitEssay } from '$lib/apis/essays';
	import { showEssaySidebar } from '$lib/stores';
	import Drawer from '../common/Drawer.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import FloppyDisk from '../icons/FloppyDisk.svelte';
	import XMark from '../icons/XMark.svelte';

	type EssayTopic = {
		id: string;
		title: string;
		question: string;
	};

	const i18n: Writable<i18nType> = getContext('i18n');

	let content = '';
	let lastSubmittedContent = '';
	let topic: EssayTopic | null = null;
	let showQuestion = false;
	let submitting = false;
	let largeScreen = false;
	let sidebarWidth = 420;
	let resizing = false;
	const minSize = 20;
	const maxSize = 50;

	export const openPane = () => {
		const container = document.getElementById('chat-container');
		if (!container) return;

		const savedWidth = parseInt(localStorage.essaySidebarSize);
		const width = savedWidth || Math.min(420, Math.floor(container.clientWidth * 0.35));
		sidebarWidth = Math.max(
			Math.floor(container.clientWidth * (minSize / 100)),
			Math.min(Math.floor(container.clientWidth * (maxSize / 100)), width)
		);
	};

	const resize = (clientX: number) => {
		const container = document.getElementById('chat-container');
		if (!container) return;

		const bounds = container.getBoundingClientRect();
		sidebarWidth = Math.max(
			Math.floor(bounds.width * (minSize / 100)),
			Math.min(Math.floor(bounds.width * (maxSize / 100)), Math.floor(bounds.right - clientX))
		);
		localStorage.essaySidebarSize = sidebarWidth;
	};

	const startResize = (event: PointerEvent) => {
		event.preventDefault();
		resizing = true;
		resize(event.clientX);
	};

	const loadWorkspace = async () => {
		try {
			const workspace = await getEssayWorkspace(localStorage.token);
			const essay = workspace?.latest_essay;
			topic = workspace?.topic ?? null;
			content = essay?.content ?? '';
			lastSubmittedContent = content;
		} catch (error) {
			console.error(error);
		}
	};

	const submit = async () => {
		const normalizedContent = content.trim();
		if (!normalizedContent || normalizedContent === lastSubmittedContent || submitting) return;

		submitting = true;
		try {
			await submitEssay(localStorage.token, normalizedContent);
			content = normalizedContent;
			lastSubmittedContent = normalizedContent;
			toast.success($i18n.t('Essay submitted successfully'));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			submitting = false;
		}
	};

	onMount(() => {
		const mediaQuery = window.matchMedia('(min-width: 1024px)');
		const handleMediaQuery = async (event: MediaQueryListEvent | MediaQueryList) => {
			largeScreen = event.matches;
			await tick();
			if (largeScreen && $showEssaySidebar) openPane();
		};

		mediaQuery.addEventListener('change', handleMediaQuery);
		handleMediaQuery(mediaQuery);
		loadWorkspace();

		return () => mediaQuery.removeEventListener('change', handleMediaQuery);
	});
</script>

<svelte:window
	on:pointermove={(event) => {
		if (resizing) resize(event.clientX);
	}}
	on:pointerup={() => (resizing = false)}
	on:pointercancel={() => (resizing = false)}
	on:resize={() => {
		if (largeScreen && $showEssaySidebar) openPane();
	}}
/>

{#snippet editor()}
	<div class="flex size-full min-h-0 min-w-0 flex-col bg-white dark:bg-gray-850">
		<div
			class="flex w-full min-w-0 shrink-0 items-center justify-between border-b border-gray-100 px-3 py-2 dark:border-gray-800"
		>
			<button
				class="min-w-0 flex-1 rounded-lg px-1 py-1 text-left transition hover:bg-gray-50 dark:hover:bg-gray-800"
				on:click={() => (showQuestion = !showQuestion)}
				disabled={!topic}
				aria-expanded={showQuestion}
			>
				<div class="flex items-center justify-between gap-2">
					<div class="truncate text-sm font-medium text-gray-900 dark:text-white">
						{topic?.title ?? $i18n.t('Essay')}
					</div>
					{#if topic}
						<div class="shrink-0 transition-transform {showQuestion ? 'rotate-180' : ''}">
							<ChevronDown className="size-3.5" />
						</div>
					{/if}
				</div>
				{#if showQuestion && topic}
					<div class="mt-2 whitespace-pre-wrap text-xs leading-5 text-gray-500">
						{topic.question}
					</div>
				{/if}
			</button>
			<button
				class="rounded-lg p-1.5 text-gray-500 transition hover:bg-gray-100 dark:hover:bg-gray-800"
				on:click={() => showEssaySidebar.set(false)}
				aria-label={$i18n.t('Close')}
			>
				<XMark className="size-4" />
			</button>
		</div>

		<textarea
			bind:value={content}
			class="min-h-0 w-full min-w-0 flex-1 resize-none bg-transparent p-4 text-sm leading-6 text-gray-900 outline-hidden placeholder:text-gray-400 dark:text-gray-100"
			placeholder={$i18n.t('Start writing your essay...')}
		></textarea>

		<div class="w-full min-w-0 shrink-0 border-t border-gray-100 p-3 dark:border-gray-800">
			<div class="mb-2 text-right text-xs text-gray-400">
				{content.trim() ? content.trim().split(/\s+/).length : 0}
				{$i18n.t('words')}
			</div>
			<button
				class="flex w-full items-center justify-center gap-2 rounded-xl bg-black px-4 py-2 text-sm font-medium text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-gray-200"
				disabled={!content.trim() || content.trim() === lastSubmittedContent || submitting}
				on:click={submit}
			>
				<FloppyDisk className="size-4" />
				{submitting
					? $i18n.t('Submitting...')
					: content.trim() === lastSubmittedContent && Boolean(content.trim())
						? $i18n.t('Submitted')
						: $i18n.t('Submit Essay')}
			</button>
		</div>
	</div>
{/snippet}

{#if !largeScreen}
	{#if $showEssaySidebar}
		<Drawer
			show={$showEssaySidebar}
			onClose={() => showEssaySidebar.set(false)}
			className="min-h-[100dvh] !bg-white dark:!bg-gray-850"
		>
			<div class="h-[100dvh]">
				{@render editor()}
			</div>
		</Drawer>
	{/if}
{:else if $showEssaySidebar}
	<div
		class="relative z-20 h-full w-px shrink-0 cursor-col-resize border-l border-gray-100 transition hover:border-gray-300 dark:border-gray-800 dark:hover:border-gray-600"
		class:bg-gray-300={resizing}
		role="separator"
		aria-orientation="vertical"
		on:pointerdown={startResize}
	>
		<div class="absolute -inset-x-2 inset-y-0"></div>
	</div>

	<aside
		class="relative z-10 h-full min-w-0 shrink-0 overflow-hidden bg-white dark:bg-gray-850"
		style:width={`${sidebarWidth}px`}
	>
		<div class="absolute inset-0 min-h-0 min-w-0 overflow-hidden">
			{@render editor()}
		</div>
	</aside>
{/if}
