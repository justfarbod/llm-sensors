<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import type { ActivationRule, PlanItem } from '$lib/apis/experiment-plans';
	import './experiment-admin.css';

	export let rule: ActivationRule;
	export let items: PlanItem[] = [];
	const i18n: Writable<i18nType> = getContext('i18n');
	const toggleTask = (id: string, enabled: boolean) => {
		rule.plan_item_ids = enabled
			? Array.from(new Set([...rule.plan_item_ids, id]))
			: rule.plan_item_ids.filter((candidate) => candidate !== id);
		rule = { ...rule };
	};
</script>

<div class="mt-3 grid gap-2 sm:grid-cols-2">
	<label class="experiment-label"
		>{$i18n.t('Activation')}
		<select class="experiment-input mt-1" bind:value={rule.mode}>
			<option value="EVERY_REQUEST">{$i18n.t('Every request')}</option><option value="FIRST_REQUEST"
				>{$i18n.t('First request only')}</option
			><option value="AFTER_PROMPT_COUNT">{$i18n.t('After prompt count')}</option><option
				value="EVERY_N_PROMPTS">{$i18n.t('Every N prompts')}</option
			><option value="PROMPT_RANGE">{$i18n.t('Prompt-number range')}</option>
		</select>
	</label>
	<label class="experiment-label"
		>{$i18n.t('Activation probability')}<input
			class="experiment-input mt-1"
			type="number"
			min="0"
			max="1"
			step="0.01"
			bind:value={rule.probability}
		/></label
	>
	{#if rule.mode === 'AFTER_PROMPT_COUNT' || rule.mode === 'EVERY_N_PROMPTS'}<label
			class="experiment-label"
			>{$i18n.t('Prompt count')}<input
				class="experiment-input mt-1"
				type="number"
				min="1"
				bind:value={rule.count}
			/></label
		>{/if}
	{#if rule.mode === 'PROMPT_RANGE'}<label class="experiment-label"
			>{$i18n.t('Range start')}<input
				class="experiment-input mt-1"
				type="number"
				min="1"
				bind:value={rule.range_start}
			/></label
		><label class="experiment-label"
			>{$i18n.t('Range end')}<input
				class="experiment-input mt-1"
				type="number"
				min="1"
				bind:value={rule.range_end}
			/></label
		>{/if}
	<label class="experiment-label"
		>{$i18n.t('Task or conversation scope')}<select
			class="experiment-input mt-1"
			bind:value={rule.scope}
			><option value="ALL_TASKS">{$i18n.t('Every task')}</option><option value="SELECTED_TASKS"
				>{$i18n.t('Selected tasks')}</option
			></select
		></label
	>
</div>
{#if rule.scope === 'SELECTED_TASKS'}
	<div class="mt-2 grid gap-1 sm:grid-cols-2">
		{#each items.filter((item) => item.id) as item}<label class="flex items-center gap-2 text-xs"
				><input
					type="checkbox"
					checked={rule.plan_item_ids.includes(item.id!)}
					on:change={(event) => toggleTask(item.id!, event.currentTarget.checked)}
				/>
				{item.title}</label
			>{/each}
	</div>
{/if}
