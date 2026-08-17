<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import type { i18n as i18nType } from 'i18next';
	import type { Writable } from 'svelte/store';

	import {
		createEssayTopic,
		deleteEssayTopic,
		getEssayTopics,
		updateEssayTopic
	} from '$lib/apis/essays';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Pencil from '$lib/components/icons/Pencil.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import MarkdownEditor from '$lib/components/common/MarkdownEditor.svelte';
	import { markdownVisibleText } from '$lib/utils/markdownEditor';
	import QuestionTasks from './QuestionTasks.svelte';
	import ExperimentWorkflows from './ExperimentWorkflows.svelte';
	import SurveyTasks from './SurveyTasks.svelte';
	import './experiment-admin.css';

	type EssayTopic = {
		id: string;
		title: string;
		question: string;
		created_at: number;
		updated_at: number;
	};

	const i18n: Writable<i18nType> = getContext('i18n');

	let topics: EssayTopic[] = [];
	let title = '';
	let question = '';
	let editingId: string | null = null;
	let saving = false;
	let loading = true;
	let activeTab: 'essay' | 'question' | 'survey' | 'plans' = 'essay';
	let topicsLoadGeneration = 0;

	const loadTopics = async () => {
		const generation = ++topicsLoadGeneration;
		loading = true;
		try {
			const response = await getEssayTopics(localStorage.token);
			if (generation !== topicsLoadGeneration) return;
			topics = Array.isArray(response) ? response : [];
		} catch (error) {
			if (generation === topicsLoadGeneration) toast.error(`${error}`);
		} finally {
			if (generation === topicsLoadGeneration) loading = false;
		}
	};

	const selectTab = async (tab: typeof activeTab) => {
		activeTab = tab;
		if (tab === 'essay') await loadTopics();
	};

	const resetForm = () => {
		title = '';
		question = '';
		editingId = null;
	};

	const editTopic = (topic: EssayTopic) => {
		editingId = topic.id;
		title = topic.title;
		question = topic.question;
	};

	const saveTopic = async () => {
		if (!title.trim() || !question.trim() || saving) return;
		saving = true;
		try {
			if (editingId) {
				await updateEssayTopic(localStorage.token, editingId, { title, question });
				toast.success($i18n.t('Essay topic updated successfully'));
			} else {
				await createEssayTopic(localStorage.token, { title, question });
				toast.success($i18n.t('Essay topic created successfully'));
			}
			resetForm();
			await loadTopics();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			saving = false;
		}
	};

	const removeTopic = async (id: string) => {
		try {
			await deleteEssayTopic(localStorage.token, id);
			if (editingId === id) resetForm();
			await loadTopics();
			toast.success($i18n.t('Essay topic deleted successfully'));
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	onMount(async () => {
		await loadTopics();
	});
</script>

<div class="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 py-4">
	<div class="flex gap-2 border-b border-gray-100 pb-2 dark:border-gray-850">
		<button
			class="rounded-lg px-3 py-2 text-sm {activeTab === 'essay'
				? 'bg-gray-100 font-medium dark:bg-gray-850'
				: 'text-gray-500'}"
			on:click={() => selectTab('essay')}>{$i18n.t('Essay Tasks')}</button
		>
		<button
			class="rounded-lg px-3 py-2 text-sm {activeTab === 'question'
				? 'bg-gray-100 font-medium dark:bg-gray-850'
				: 'text-gray-500'}"
			on:click={() => selectTab('question')}>{$i18n.t('Question Tasks')}</button
		>
		<button
			class="rounded-lg px-3 py-2 text-sm {activeTab === 'survey'
				? 'bg-gray-100 font-medium dark:bg-gray-850'
				: 'text-gray-500'}"
			on:click={() => selectTab('survey')}>{$i18n.t('Survey Tasks')}</button
		>
		<button
			class="rounded-lg px-3 py-2 text-sm {activeTab === 'plans'
				? 'bg-gray-100 font-medium dark:bg-gray-850'
				: 'text-gray-500'}"
			on:click={() => selectTab('plans')}>{$i18n.t('Workflows')}</button
		>
	</div>
	{#if activeTab === 'plans'}
		<ExperimentWorkflows onResourcesImported={loadTopics} />
	{:else if activeTab === 'question'}
		<QuestionTasks />
	{:else if activeTab === 'survey'}
		<SurveyTasks />
	{:else}
		<div class="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
			<section class="min-w-0 space-y-4">
				<form
					class="rounded-2xl border border-gray-100 p-4 dark:border-gray-850"
					on:submit={(event) => {
						event.preventDefault();
						saveTopic();
					}}
				>
					<div class="flex flex-wrap items-start justify-between gap-2">
						<div>
							<h2 class="font-medium">
								{editingId ? $i18n.t('Edit Essay Topic') : $i18n.t('New Essay Topic')}
							</h2>
							<p class="mt-1 text-xs text-gray-500">
								{$i18n.t('Create topics that can be selected in experiment workflows.')}
							</p>
						</div>
						<button type="button" class="essay-button" on:click={resetForm}>
							{$i18n.t('New')}
						</button>
					</div>
					<div class="mt-4 space-y-3">
						<label class="experiment-label">
							{$i18n.t('Topic title')}
							<input class="experiment-input" bind:value={title} required />
						</label>
						<div class="experiment-label">
							<span>{$i18n.t('Essay question')}</span>
							<MarkdownEditor
								bind:value={question}
								required
								ariaLabel={$i18n.t('Essay question')}
								className="min-h-40"
								textareaClass="min-h-32 resize-y"
							/>
						</div>
					</div>
					<div class="mt-4 flex justify-end gap-2">
						{#if editingId}
							<button type="button" class="essay-button" on:click={resetForm}>
								<XMark className="size-3.5" />
								{$i18n.t('Cancel')}
							</button>
						{/if}
						<button
							type="submit"
							disabled={saving || !title.trim() || !question.trim()}
							class="essay-primary"
						>
							<Plus className="size-3" />
							{editingId ? $i18n.t('Update Topic') : $i18n.t('Save Topic')}
						</button>
					</div>
				</form>
			</section>

			<aside class="min-w-0">
				<h2 class="mb-2 text-sm font-medium">{$i18n.t('Essay Topics')}</h2>
				{#if loading}<p class="py-8 text-center text-sm text-gray-500">
						{$i18n.t('Loading...')}
					</p>{/if}
				<div class="space-y-2">
					{#each topics as topic (topic.id)}
						<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-850">
							<button class="w-full min-w-0 text-left" on:click={() => editTopic(topic)}>
								<div class="truncate text-sm font-medium">{topic.title}</div>
								<div class="mt-1 line-clamp-3 whitespace-pre-wrap text-xs leading-5 text-gray-500">
									{markdownVisibleText(topic.question)}
								</div>
							</button>
							<div class="mt-2 flex flex-wrap gap-1">
								<button class="essay-button" on:click={() => editTopic(topic)}>
									<Pencil className="size-3.5" />
									{$i18n.t('Edit')}
								</button>
								<button class="essay-button text-red-600" on:click={() => removeTopic(topic.id)}>
									<GarbageBin className="size-3.5" />
									{$i18n.t('Delete')}
								</button>
							</div>
						</div>
					{:else}
						{#if !loading}<p class="py-8 text-center text-sm text-gray-500">
								{$i18n.t('No essay topics yet.')}
							</p>{/if}
					{/each}
				</div>
			</aside>
		</div>
	{/if}
</div>

<style>
	:global(.essay-button) {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		border-radius: 0.6rem;
		background: rgb(243 244 246);
		padding: 0.45rem 0.7rem;
		font-size: 0.75rem;
		font-weight: 500;
	}
	:global(.dark .essay-button) {
		background: rgb(31 41 55);
	}
	:global(.essay-primary) {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		border-radius: 9999px;
		background: black;
		padding: 0.55rem 1rem;
		font-size: 0.75rem;
		font-weight: 500;
		color: white;
	}
	:global(.dark .essay-primary) {
		background: white;
		color: black;
	}
	:global(.essay-primary:disabled) {
		opacity: 0.4;
	}
</style>
