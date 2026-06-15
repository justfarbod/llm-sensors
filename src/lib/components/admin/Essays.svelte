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

	const loadTopics = async () => {
		loading = true;
		try {
			const response = await getEssayTopics(localStorage.token);
			topics = Array.isArray(response) ? response : [];
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
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

<div class="mx-auto flex w-full max-w-5xl flex-col gap-4 px-4 py-4">
	<div>
		<div class="text-xl font-medium">{$i18n.t('Essay Topics')}</div>
		<div class="mt-1 text-sm text-gray-500">
			{$i18n.t('Create the topics that can be assigned to essay-enabled groups.')}
		</div>
	</div>

	<form
		class="rounded-3xl border border-gray-100 bg-white p-4 dark:border-gray-850 dark:bg-gray-900"
		on:submit={(event) => {
			event.preventDefault();
			saveTopic();
		}}
	>
		<div class="mb-3 flex items-center justify-between">
			<div class="text-sm font-medium">
				{editingId ? $i18n.t('Edit Essay Topic') : $i18n.t('Add Essay Topic')}
			</div>
			{#if editingId}
				<button
					type="button"
					class="rounded-lg p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
					on:click={resetForm}
					aria-label={$i18n.t('Cancel')}
				>
					<XMark className="size-4" />
				</button>
			{/if}
		</div>

		<input
			bind:value={title}
			class="w-full rounded-xl bg-gray-50 px-3 py-2 text-sm outline-hidden dark:bg-gray-850"
			placeholder={$i18n.t('Topic title')}
			required
		/>
		<textarea
			bind:value={question}
			class="mt-2 min-h-28 w-full resize-y rounded-xl bg-gray-50 px-3 py-2 text-sm outline-hidden dark:bg-gray-850"
			placeholder={$i18n.t('Essay question')}
			required
		></textarea>

		<div class="mt-3 flex justify-end">
			<button
				type="submit"
				disabled={saving || !title.trim() || !question.trim()}
				class="flex items-center gap-1.5 rounded-full bg-black px-4 py-2 text-xs font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
			>
				<Plus className="size-3" />
				{editingId ? $i18n.t('Update Topic') : $i18n.t('Add Topic')}
			</button>
		</div>
	</form>

	<div class="flex flex-col gap-2">
		{#if loading}
			<div class="py-16 text-center text-sm text-gray-500">{$i18n.t('Loading...')}</div>
		{:else}
			{#each topics as topic (topic.id)}
				<div
					class="rounded-2xl border border-gray-100 bg-white px-4 py-3 dark:border-gray-850 dark:bg-gray-900"
				>
					<div class="flex items-start justify-between gap-3">
						<div class="min-w-0">
							<div class="text-sm font-medium">{topic.title}</div>
							<div class="mt-1 whitespace-pre-wrap text-xs leading-5 text-gray-500">
								{topic.question}
							</div>
						</div>
						<div class="flex shrink-0 gap-1">
							<button
								class="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
								on:click={() => editTopic(topic)}
								aria-label={$i18n.t('Edit')}
							>
								<Pencil className="size-3.5" />
							</button>
							<button
								class="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-red-600 dark:hover:bg-gray-800"
								on:click={() => removeTopic(topic.id)}
								aria-label={$i18n.t('Delete')}
							>
								<GarbageBin className="size-3.5" />
							</button>
						</div>
					</div>
				</div>
			{:else}
				<div class="py-16 text-center text-sm text-gray-500">
					{$i18n.t('No essay topics yet.')}
				</div>
			{/each}
		{/if}
	</div>
</div>
