<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { quintOut } from 'svelte/easing';
	import { slide } from 'svelte/transition';
	import { toast } from 'svelte-sonner';
	import type { i18n as i18nType } from 'i18next';
	import type { Writable } from 'svelte/store';

	import { getEssayWorkspace, submitEssay } from '$lib/apis/essays';
	import { experimentCurrent, experimentRefresh, showEssaySidebar } from '$lib/stores';
	import Drawer from '../common/Drawer.svelte';
	import MarkdownEditor from '../common/MarkdownEditor.svelte';
	import SafeMarkdown from '../common/SafeMarkdown.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import FloppyDisk from '../icons/FloppyDisk.svelte';
	import XMark from '../icons/XMark.svelte';
	import { flushExperimentTelemetry } from '$lib/utils/experimentTelemetry';
	import { markdownTextMetrics } from '$lib/utils/markdownEditor';

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
	$: wordCount = markdownTextMetrics(content).wordCount;
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
			if (
				$experimentCurrent?.telemetry_extension?.required &&
				!(await flushExperimentTelemetry(true))
			)
				throw new Error($i18n.t('Interaction telemetry could not be saved. Please try again.'));
			await submitEssay(localStorage.token, normalizedContent);
			content = normalizedContent;
			lastSubmittedContent = normalizedContent;
			toast.success($i18n.t('Essay submitted successfully'));
			experimentRefresh.update((value) => value + 1);
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
			class="w-full min-w-0 shrink-0 border-b border-gray-100 px-3 py-2 transition-colors duration-200 dark:border-gray-800 {showQuestion
				? 'bg-gray-50/60 dark:bg-gray-900/40'
				: 'bg-white dark:bg-gray-850'}"
		>
			<div class="flex items-center justify-between gap-2">
				<button
					type="button"
					class="group min-w-0 flex-1 rounded-xl px-2 py-1.5 text-left transition-colors hover:bg-gray-100/80 dark:hover:bg-gray-800/80"
					on:click={() => (showQuestion = !showQuestion)}
					disabled={!topic}
					aria-expanded={showQuestion}
					aria-controls="legacy-essay-instructions"
				>
					<div class="flex items-center justify-between gap-3">
						<div class="min-w-0">
							<div class="text-[10px] font-semibold uppercase tracking-wider text-gray-400">
								{$i18n.t('Essay instructions')}
							</div>
							<div class="truncate text-sm font-medium text-gray-900 dark:text-white">
								{topic?.title ?? $i18n.t('Essay')}
							</div>
						</div>
						{#if topic}
							<div
								class="flex size-7 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-500 transition-all duration-300 group-hover:bg-gray-200 group-hover:text-gray-700 dark:bg-gray-800 dark:text-gray-400 dark:group-hover:bg-gray-700 dark:group-hover:text-gray-200 {showQuestion
									? 'rotate-180'
									: ''}"
							>
								<ChevronDown className="size-3.5" />
							</div>
						{/if}
					</div>
				</button>
				<button
					class="rounded-lg p-1.5 text-gray-500 transition hover:bg-gray-100 dark:hover:bg-gray-800"
					on:click={() => showEssaySidebar.set(false)}
					aria-label={$i18n.t('Close')}
				>
					<XMark className="size-4" />
				</button>
			</div>
			{#if showQuestion && topic}
				<div
					id="legacy-essay-instructions"
					transition:slide={{ duration: 240, easing: quintOut, axis: 'y' }}
				>
					<div
						class="mx-2 mt-1 max-h-[30dvh] overflow-y-auto rounded-xl border border-gray-100 bg-white/80 px-3 py-2.5 text-xs leading-5 text-gray-500 shadow-xs dark:border-gray-800 dark:bg-gray-850/80"
					>
						<SafeMarkdown content={topic.question} className="markdown-prose-xs" />
					</div>
				</div>
			{/if}
		</div>

		<MarkdownEditor
			bind:value={content}
			experimentField="essay"
			className="min-h-40 flex-1 !rounded-none !border-0 !ring-0"
			textareaClass="p-4"
			ariaLabel={$i18n.t('Essay')}
			placeholder={$i18n.t('Start writing your essay...')}
		/>

		<div class="w-full min-w-0 shrink-0 border-t border-gray-100 p-3 dark:border-gray-800">
			<div class="mb-2 text-right text-xs text-gray-400">
				{wordCount}
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
