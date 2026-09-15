<script lang="ts">
	export let tasks: any[] = [];
	export let fmtDate: (value: number | null) => string;
	export let onOpenQuestion: (submissionId: string) => void;
	const taskLabels: Record<string, string> = {
		ESSAY: 'Essay',
		QUESTION: 'Questions',
		SURVEY: 'Survey'
	};
</script>

{#if tasks.length}
	<h3 class="mb-2 mt-6 font-semibold">Workflow task timeline</h3>
	<div class="space-y-3">
		{#each tasks as task}
			<section class="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h4 class="font-semibold">{task.position + 1}. {task.title}</h4>
					<span class="text-xs text-gray-500">{taskLabels[task.task_type]} · {task.status}</span>
				</div>
				<p class="mt-2 text-xs text-gray-500">
					Started: {fmtDate(task.started_at)} · Completed: {fmtDate(
						task.finalized_at ?? task.completed_at
					)}
				</p>
				{#if task.task_type === 'QUESTION'}
					{#if task.question_submission}
						{@const submission = task.question_submission}
						<p class="mt-3 text-sm">
							Score: {submission.score ?? 'Pending'} / {submission.maximum_score}
							{#if submission.score == null}
								· Provisional: {submission.provisional_score ?? 0}
							{/if}
							· {submission.grading_status}
						</p>
						<p class="mt-1 text-xs text-gray-500">
							{submission.status} · Submitted: {fmtDate(submission.submitted_at)}
						</p>
						<button
							class="mt-3 rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700"
							on:click={() => onOpenQuestion(submission.submission_id)}
							>View answers and grading</button
						>
					{:else}
						<p class="mt-3 text-sm text-gray-500">No question submission yet.</p>
					{/if}
				{:else if task.task_type === 'ESSAY' && task.essay}
					<p class="mt-3 text-sm font-medium">{task.essay.topic_title}</p>
					<p class="mt-1 text-xs text-gray-500">
						{task.essay.is_draft ? 'Current draft' : 'Submitted'} · {task.essay.word_count} words
					</p>
					<div class="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap text-sm leading-6">
						{task.essay.content || 'No essay text yet.'}
					</div>
				{:else if task.task_type === 'SURVEY'}
					{#if task.survey_submission}
						{@const submission = task.survey_submission}
						<p class="mt-3 text-xs text-gray-500">
							{submission.status} · {task.required ? 'Required' : 'Optional'} · {fmtDate(
								submission.submitted_at ?? submission.skipped_at
							)}
						</p>
						{#if submission.status !== 'SKIPPED'}
							<dl class="mt-3 space-y-3 text-sm">
								{#each submission.answers as answer}
									<div>
										<dt class="font-medium">{answer.prompt}</dt>
										<dd class="mt-1 whitespace-pre-wrap text-gray-600 dark:text-gray-300">
											{Array.isArray(answer.value)
												? answer.value.join(', ')
												: (answer.value ?? 'Not answered')}
										</dd>
									</div>
								{/each}
							</dl>
						{/if}
					{:else}
						<p class="mt-3 text-sm text-gray-500">No survey submission yet.</p>
					{/if}
				{/if}
				{#if task.usage}<p class="mt-3 text-xs text-gray-500">
						{task.usage.prompts} prompts · {task.usage.responses} responses
					</p>{/if}
				{#if task.messages?.length}<details class="mt-3">
						<summary class="cursor-pointer text-sm font-medium">Task conversation</summary>
						<div class="mt-3 space-y-3">
							{#each task.messages as message}<div
									class="rounded-lg bg-gray-50 p-3 dark:bg-gray-850"
								>
									<p class="text-xs text-gray-500">
										{message.role} · {message.model_id ?? ''} · {fmtDate(message.created_at)}
									</p>
									<p class="mt-1 whitespace-pre-wrap text-sm">
										{typeof message.content === 'string'
											? message.content
											: JSON.stringify(message.content)}
									</p>
								</div>{/each}
						</div>
					</details>{/if}
				{#if task.telemetry}<details class="mt-3">
						<summary class="cursor-pointer text-sm font-medium"
							>Task telemetry ({task.telemetry.events.length} events)</summary
						>
						<p class="mt-2 text-xs text-gray-500">
							Elapsed task time and active-time telemetry are separate measures.
						</p>
						<pre class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(
								task.telemetry,
								null,
								2
							)}</pre>
					</details>{/if}
			</section>
		{/each}
	</div>
{/if}
