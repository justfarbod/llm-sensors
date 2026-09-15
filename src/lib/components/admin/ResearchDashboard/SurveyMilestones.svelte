<script lang="ts">
	export let progress: any;
	export let fmtDate: (value: number) => string;
</script>

<div class="space-y-1 text-xs">
	{#each ['pre', 'post'] as phase}
		{@const completed = progress[`${phase}_survey_completed`]}
		{@const submittedAt = progress[`${phase}_survey_submitted_at`]}
		{@const skippedAt = progress[`${phase}_survey_skipped_at`]}
		{@const startedAt = progress[`${phase}_survey_started_at`]}
		{#if completed || skippedAt != null || startedAt != null}
			<div>
				{phase === 'pre' ? 'Pre-survey' : 'Post-survey'}:
				{completed ? 'Completed' : skippedAt != null ? 'Skipped' : 'In progress'}
				{#if submittedAt != null || skippedAt != null}
					· {fmtDate(submittedAt ?? skippedAt)}
				{/if}
			</div>
		{/if}
	{/each}
</div>
