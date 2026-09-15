<script lang="ts">
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ParticipantTasks from './ParticipantTasks.svelte';
	import Pagination from './Pagination.svelte';
	export let detailLoading: any;
	export let detail: any;
	export let anonymized: any;
	export let tabActivity: any;
	export let tabActivityLoading: any;
	export let tabEventType: any;
	export let tabBrowserId: any;
	export let tabWindowId: any;
	export let tabDateFrom: any;
	export let tabDateTo: any;
	export let tabActivityPage: any;
	export let closeDetail: any;
	export let runFullSessionExport: any;
	export let fmtDate: any;
	export let openQuestionDetail: any;
	export let fmtDuration: any;
	export let loadTabActivity: any;
</script>

{#if detailLoading || detail}
	<div class="fixed inset-0 z-50 flex justify-end">
		<button
			class="absolute inset-0 bg-black/30"
			aria-label="Close session detail"
			on:click={closeDetail}
		></button>
		<aside
			class="relative h-full w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-2xl dark:bg-gray-900"
			aria-label="Session detail"
		>
			<div class="flex items-center justify-between">
				<h2 class="text-xl font-semibold">Session detail</h2>
				<button class="research-button" on:click={closeDetail}>Close</button>
			</div>
			{#if detailLoading}<div class="flex h-64 items-center justify-center"><Spinner /></div>
			{:else}
				<p class="mt-2 text-sm text-gray-500">
					{detail.participant_id} · {detail.name ?? 'Not available'} · {detail.state}
				</p>
				<p class="mt-2 text-sm">
					{detail.workflow_name} · Applied v{detail.plan_version} · {detail.condition?.name}
				</p>
				{#if detail.is_demo}<span class="rounded bg-amber-100 px-2 py-1 text-xs text-amber-900"
						>Synthetic demo</span
					>{/if}
				<div class="mt-3 flex flex-wrap items-center gap-2">
					<label
						class="text-xs text-gray-500"
						title="Free-form text, chat content, and telemetry URLs are not redacted."
						><input type="checkbox" bind:checked={anonymized} /> Anonymize account identifiers</label
					>
					<button class="research-button" on:click={() => runFullSessionExport([detail.session_id])}
						>Export full session (JSON)</button
					>
					<p class="w-full text-xs text-amber-700 dark:text-amber-300">
						Essays, chats, survey text, tab titles, and URLs remain unchanged.
					</p>
				</div>
				<ParticipantTasks
					tasks={detail.tasks ?? []}
					{fmtDate}
					onOpenQuestion={openQuestionDetail}
				/>
				<h3 class="mb-2 mt-6 font-semibold">LLM usage and durations</h3>
				<p class="text-sm">
					Prompts: {detail.prompts} · Responses: {detail.responses} · Input tokens: {detail.input_tokens}
					· Output tokens: {detail.output_tokens} · Total tokens: {detail.total_tokens}
				</p>
				<p class="mt-2 text-sm">
					Run duration: {fmtDuration(detail.session_duration)} · Usage attribution: {detail.usage
						?.attribution}
				</p>
				{#if detail.unattributed_messages?.length}<details class="mt-4">
						<summary class="cursor-pointer text-sm font-medium"
							>Conversation without a step assignment</summary
						>{#each detail.unattributed_messages as message}<p
								class="mt-2 whitespace-pre-wrap text-sm"
							>
								<strong>{message.role}:</strong>
								{typeof message.content === 'string'
									? message.content
									: JSON.stringify(message.content)}
							</p>{/each}
					</details>{/if}
				<h3 class="mb-2 mt-6 font-semibold">Interaction telemetry</h3>
				<div class="grid gap-2 sm:grid-cols-2">
					{#each Object.entries(detail.telemetry_summary ?? {}) as [name, value]}<div
							class="rounded-xl bg-gray-50 p-3 dark:bg-gray-850"
						>
							<div class="text-xs text-gray-400">{name.replaceAll('_', ' ')}</div>
							<div class="mt-1 text-sm">{value ?? 'Not available'}</div>
						</div>{/each}
				</div>
				<div class="mt-6 rounded-2xl border border-amber-300 p-4 dark:border-amber-700">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<div>
							<h3 class="font-semibold">Sensitive tab activity</h3>
							<p class="mt-1 text-xs text-amber-700 dark:text-amber-300">
								Contains complete URLs and titles. Anonymized exports do not redact them.
							</p>
						</div>
						<div class="flex flex-wrap gap-2">
							<button class="research-button" on:click={() => loadTabActivity(detail.session_id, 1)}
								>{tabActivity ? 'Refresh timeline' : 'Load timeline'}</button
							>
						</div>
					</div>
					{#if tabActivity}
						<div class="mt-3 flex flex-wrap items-end gap-2">
							<label class="text-xs" for="tab-event-filter">Event</label>
							<select
								id="tab-event-filter"
								class="rounded-lg bg-gray-100 px-2 py-1 text-xs dark:bg-gray-800"
								bind:value={tabEventType}
								on:change={() => loadTabActivity(detail.session_id, 1)}
							>
								<option value="">All events</option>
								{#each ['tab_snapshot', 'tab_created', 'tab_updated', 'tab_activated', 'tab_highlighted', 'tab_moved', 'tab_attached', 'tab_detached', 'tab_replaced', 'tab_removed', 'window_focus_changed', 'telemetry_loss'] as eventName}<option
										value={eventName}>{eventName.replaceAll('_', ' ')}</option
									>{/each}
							</select>
							<label class="text-xs" for="tab-id-filter">Tab ID</label>
							<input
								id="tab-id-filter"
								type="number"
								class="w-24 rounded-lg bg-gray-100 px-2 py-1 text-xs dark:bg-gray-800"
								bind:value={tabBrowserId}
							/>
							<label class="text-xs" for="window-id-filter">Window ID</label>
							<input
								id="window-id-filter"
								type="number"
								class="w-24 rounded-lg bg-gray-100 px-2 py-1 text-xs dark:bg-gray-800"
								bind:value={tabWindowId}
							/>
							<label class="text-xs" for="tab-from-filter">From</label>
							<input
								id="tab-from-filter"
								type="datetime-local"
								class="rounded-lg bg-gray-100 px-2 py-1 text-xs dark:bg-gray-800"
								bind:value={tabDateFrom}
							/>
							<label class="text-xs" for="tab-to-filter">To</label>
							<input
								id="tab-to-filter"
								type="datetime-local"
								class="rounded-lg bg-gray-100 px-2 py-1 text-xs dark:bg-gray-800"
								bind:value={tabDateTo}
							/>
							<button class="research-button" on:click={() => loadTabActivity(detail.session_id, 1)}
								>Apply filters</button
							>
						</div>
						{#if tabActivityLoading}<div class="flex h-24 items-center justify-center">
								<Spinner />
							</div>
						{:else}<div class="mt-3 max-h-96 space-y-2 overflow-y-auto">
								{#each tabActivity.items ?? [] as event}<details
										class="rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-850"
									>
										<summary class="cursor-pointer break-all font-medium">
											{fmtDate(event.event_time)} · {event.event_type} · tab {event.tab_id ?? '—'} ·
											{event.title ?? event.url ?? 'No title'}
										</summary>
										{#if event.url}<p class="mt-2 break-all text-blue-700 dark:text-blue-300">
												{event.url}
											</p>{/if}
										<pre class="research-json mt-2">{JSON.stringify(event.payload, null, 2)}</pre>
									</details>{:else}<p class="py-6 text-center text-xs text-gray-500">
										No matching tab events.
									</p>{/each}
							</div>
							<Pagination
								total={tabActivity.total ?? 0}
								page={tabActivityPage}
								limit={100}
								onPage={(next) => loadTabActivity(detail.session_id, next)}
							/>
						{/if}
					{/if}
				</div>
				<h3 class="mb-2 mt-6 font-semibold">Experiment condition and LLM perturbations</h3>
				<p class="text-sm">
					Condition: <strong>{detail.condition?.name ?? 'Implicit control'}</strong> · Revision:
					{detail.configuration_revision ?? 'Legacy'}
				</p>
				<p class="mt-1 text-xs text-gray-500">
					{detail.configuration_summary?.prompt_injection_enabled
						? 'Prompt injection; '
						: ''}{detail.configuration_summary?.warning_modal_enabled
						? 'Warning modal; '
						: ''}Timing: {detail.configuration_summary?.response_timing_mode ?? 'NORMAL'}
				</p>
				<div class="mt-3 space-y-2">
					{#each detail.perturbation_requests ?? [] as request}
						<details class="rounded-xl bg-gray-50 p-3 text-xs dark:bg-gray-850">
							<summary class="cursor-pointer font-medium"
								>Request {request.request_sequence} · prompt {request.prompt_number} · {request.timing_mode}
								· {request.status}</summary
							>
							<pre class="research-json mt-2">{JSON.stringify(request, null, 2)}</pre>
						</details>
					{/each}
				</div>
				{#if (detail.perturbation_events ?? []).length}<h4 class="mb-2 mt-4 text-sm font-semibold">
						Modal and participant-visible timing events
					</h4>
					<pre class="research-json">{JSON.stringify(
							detail.perturbation_events,
							null,
							2
						)}</pre>{/if}
				{#if detail.pre_survey || !detail.tasks?.length}
					<h3 class="mb-2 mt-6 font-semibold">Pre-survey</h3>
					<pre class="research-json">{JSON.stringify(
							detail.pre_survey ?? 'Not available',
							null,
							2
						)}</pre>
				{/if}
				{#if detail.post_survey || !detail.tasks?.length}
					<h3 class="mb-2 mt-6 font-semibold">Post-survey</h3>
					<pre class="research-json">{JSON.stringify(
							detail.post_survey ?? 'Not available',
							null,
							2
						)}</pre>
				{/if}
			{/if}
		</aside>
	</div>
{/if}
