<script lang="ts">
	import { getContext } from 'svelte';
	import { activePromptBudget } from '$lib/stores/experimentPromptBudgets';
	const i18n = getContext<any>('i18n');
</script>

{#if $activePromptBudget}
	<div class="px-3 py-2 text-xs text-gray-600 dark:text-gray-300" role="status" aria-live="polite">
		{#if $activePromptBudget.usage}
			<span class="font-medium">{$activePromptBudget.label}:</span>
			{$i18n.t('Prompts used: {{used}} / {{limit}}', $activePromptBudget.usage)}
			· {$i18n.t('{{remaining}} remaining', $activePromptBudget.usage)}
			{#if $activePromptBudget.usage.pending}
				· {$i18n.t('{{pending}} in progress', $activePromptBudget.usage)}
			{/if}
			{#if $activePromptBudget.usage.available === 0}
				<div>
					{$i18n.t(
						$activePromptBudget.usage.remaining === 0
							? 'Prompt limit reached. You can continue working on your answer.'
							: 'Your remaining prompts are in progress.'
					)}
				</div>
			{/if}
		{:else}
			{$i18n.t('Select a task question to see your prompt allowance.')}
		{/if}
	</div>
{/if}
