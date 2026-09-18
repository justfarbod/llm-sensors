<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { i18n as i18nType } from 'i18next';
	import type { Writable } from 'svelte/store';

	import Switch from '$lib/components/common/Switch.svelte';
	import { getEssayTopics } from '$lib/apis/essays';

	import { DEFAULT_PERMISSIONS } from '$lib/constants/permissions';

	type EssayTopic = {
		id: string;
		title: string;
		question: string;
	};

	const i18n: Writable<i18nType> = getContext('i18n');

	export let permissions: any = {};
	export let defaultPermissions: any = {};
	export let data: any = {};
	export let custom = true;

	let essayTopics: EssayTopic[] = [];

	// Reactive statement to ensure all fields are present in `permissions`
	$: {
		permissions = fillMissingProperties(permissions, DEFAULT_PERMISSIONS);
	}

	function fillMissingProperties(obj: any, defaults: any) {
		return {
			...defaults,
			...obj,
			workspace: { ...defaults.workspace, ...obj.workspace },
			sharing: { ...defaults.sharing, ...obj.sharing },
			access_grants: { ...defaults.access_grants, ...obj.access_grants },
			chat: { ...defaults.chat, ...obj.chat },
			features: { ...defaults.features, ...obj.features },
			settings: { ...defaults.settings, ...obj.settings }
		};
	}

	const setEssayTopicConfig = (values: Record<string, unknown>) => {
		data = {
			...data,
			config: {
				...(data?.config ?? {}),
				...values
			}
		};
	};

	const setExperimentMode = (enabled: boolean) => setEssayTopicConfig({ experiment_mode_enabled: enabled });

	onMount(async () => {
		permissions = fillMissingProperties(permissions, DEFAULT_PERMISSIONS);
		if (custom) {
			essayTopics = await getEssayTopics(localStorage.token).catch(() => []);
		}
	});
</script>

<div class="space-y-2">
	{#if custom}
		<div>
			<div class=" mb-2 text-sm font-medium">{$i18n.t('Experiment Mode')}</div>

			<div class="flex flex-col gap-2 pb-1 pt-0.5">
				<div class="flex w-full items-center justify-between gap-3">
					<div class="text-xs">{$i18n.t('Enable Experiment Mode for this group')}</div>
					<Switch
						state={data?.config?.experiment_mode_enabled ?? false}
						on:change={(event) => setExperimentMode(event.detail)}
					/>
				</div>
				<div class="flex w-full items-center justify-between gap-3">
					<div class="text-xs">{$i18n.t('Topic assignment')}</div>
					<select
						class="max-w-56 rounded-lg bg-gray-50 px-2 py-1 text-xs outline-hidden dark:bg-gray-850"
						value={data?.config?.essay_topic_mode ?? 'random'}
						on:change={(event) => {
							const mode = event.currentTarget.value;
							setEssayTopicConfig({
								essay_topic_mode: mode,
								essay_topic_id:
									mode === 'specific'
										? (data?.config?.essay_topic_id ?? essayTopics[0]?.id ?? null)
										: null
							});
						}}
					>
						<option value="random">{$i18n.t('Random topic per user')}</option>
						<option value="specific">{$i18n.t('Specific topic')}</option>
					</select>
				</div>

				{#if (data?.config?.essay_topic_mode ?? 'random') === 'specific'}
					<div class="flex w-full items-center justify-between gap-3">
						<div class="text-xs">{$i18n.t('Essay topic')}</div>
						<select
							class="max-w-56 rounded-lg bg-gray-50 px-2 py-1 text-xs outline-hidden dark:bg-gray-850"
							value={data?.config?.essay_topic_id ?? ''}
							on:change={(event) =>
								setEssayTopicConfig({ essay_topic_id: event.currentTarget.value })}
							required
						>
							<option value="" disabled>{$i18n.t('Select a topic')}</option>
							{#each essayTopics as topic (topic.id)}
								<option value={topic.id}>{topic.title}</option>
							{/each}
						</select>
					</div>
					{#if essayTopics.length === 0}
						<div class="text-xs text-amber-600">
							{$i18n.t('Add an essay topic in the Essays admin tab first.')}
						</div>
					{/if}
				{/if}
			</div>
		</div>
	{/if}
</div>
