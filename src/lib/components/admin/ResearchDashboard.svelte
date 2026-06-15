<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page as pageStore } from '$app/stores';
	import { toast } from 'svelte-sonner';

	import {
		exportResearchData,
		getResearchFilters,
		getResearchSection,
		getResearchSession
	} from '$lib/apis/experiment-analytics';
	import MetricCards from './ResearchDashboard/MetricCards.svelte';
	import ResearchBars from './ResearchDashboard/ResearchBars.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Pagination from './ResearchDashboard/Pagination.svelte';

	const tabs = [
		{ id: 'overview', label: 'Overview' },
		{ id: 'participants', label: 'Participants' },
		{ id: 'essays', label: 'Essays' },
		{ id: 'usage', label: 'LLM Usage' },
		{ id: 'essay-stats', label: 'Essay Statistics' },
		{ id: 'surveys', label: 'Survey Results' }
	];
	const metricLabels = {
		total_sessions: 'Total sessions',
		active_sessions: 'Active sessions',
		completed_sessions: 'Completed sessions',
		assigned_participants: 'Assigned participants',
		consented: 'Consent received',
		pre_survey_completed: 'Pre-survey completed',
		essays_submitted: 'Essays submitted',
		post_survey_completed: 'Post-survey completed',
		completion_rate: 'Completion rate',
		average_session_duration: 'Average session duration',
		average_time_to_essay: 'Average writing time',
		average_essay_to_post_survey: 'Essay to post-survey',
		total_prompts: 'Total prompts',
		total_responses: 'Assistant responses',
		total_tokens: 'Total tokens',
		average_prompts_per_completed_essay: 'Prompts per submitted essay',
		average_tokens_per_completed_essay: 'Tokens per submitted essay',
		average_word_count: 'Average word count',
		median_word_count: 'Median word count',
		minimum_word_count: 'Minimum word count',
		maximum_word_count: 'Maximum word count'
	};

	let mounted = false;
	let activeTab = 'overview';
	let loadedTab = '';
	let availableFilters: any = { groups: [], topics: [], states: [] };
	let filters: Record<string, string> = {
		group_id: '',
		topic_id: '',
		date_from: '',
		date_to: '',
		state: '',
		completed: '',
		search: ''
	};
	let page = 1;
	let limit = 25;
	let orderBy = '';
	let direction: 'asc' | 'desc' = 'desc';
	let data: any = null;
	let loading = true;
	let error = '';
	let selected = new Set<string>();
	let anonymized = true;
	let detail: any = null;
	let detailLoading = false;
	let loadTimer: ReturnType<typeof setTimeout>;
	let controller: AbortController | null = null;

	$: activeTab = tabs.some((tab) => tab.id === $pageStore.params.tab)
		? ($pageStore.params.tab ?? 'overview')
		: 'overview';
	$: if (mounted && activeTab !== loadedTab) {
		page = 1;
		selected = new Set();
		scheduleLoad(0);
	}

	const epoch = (date: string, end = false) =>
		date ? Math.floor(new Date(`${date}T${end ? '23:59:59' : '00:00:00'}`).getTime() / 1000) : '';

	const requestFilters = () => ({
		group_id: filters.group_id,
		topic_id: filters.topic_id,
		date_from: epoch(filters.date_from),
		date_to: epoch(filters.date_to, true),
		state: filters.state,
		completed: filters.completed,
		search: ['participants', 'essays'].includes(activeTab) ? filters.search : '',
		page,
		limit,
		order_by: orderBy,
		direction
	});

	const syncUrl = () => {
		const query = new URLSearchParams();
		for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
		goto(`/admin/analytics/${activeTab}${query.size ? `?${query}` : ''}`, {
			replaceState: true,
			noScroll: true,
			keepFocus: true
		});
	};

	const scheduleLoad = (delay = 250) => {
		clearTimeout(loadTimer);
		loadTimer = setTimeout(load, delay);
	};

	const load = async () => {
		controller?.abort();
		controller = new AbortController();
		loading = true;
		error = '';
		try {
			data = await getResearchSection(
				localStorage.token,
				activeTab,
				requestFilters(),
				controller.signal
			);
			loadedTab = activeTab;
		} catch (message) {
			if (!controller.signal.aborted) error = String(message);
		} finally {
			if (!controller.signal.aborted) loading = false;
		}
	};

	const filtersChanged = () => {
		page = 1;
		syncUrl();
		scheduleLoad();
	};

	const changePage = (next: number) => {
		page = next;
		scheduleLoad(0);
	};

	const sortBy = (key: string) => {
		direction = orderBy === key && direction === 'desc' ? 'asc' : 'desc';
		orderBy = key;
		page = 1;
		scheduleLoad(0);
	};

	const toggleSelected = (id: string) => {
		const next = new Set(selected);
		next.has(id) ? next.delete(id) : next.add(id);
		selected = next;
	};

	const openDetail = async (sessionId: string) => {
		detail = null;
		detailLoading = true;
		try {
			detail = await getResearchSession(localStorage.token, sessionId);
		} catch (message) {
			toast.error(String(message));
		} finally {
			detailLoading = false;
		}
	};

	const runExport = async (
		section: 'participants' | 'essays' | 'surveys',
		ids: string[],
		format: 'csv' | 'json'
	) => {
		if (!ids.length) return toast.info('Select at least one record to export.');
		try {
			const blob = await exportResearchData(localStorage.token, section, ids, format, anonymized);
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = `experiment-${section}.${format}`;
			document.body.appendChild(link);
			link.click();
			link.remove();
			setTimeout(() => URL.revokeObjectURL(url), 0);
		} catch (message) {
			toast.error(String(message));
		}
	};
	const exportSelected = (format: 'csv' | 'json') =>
		runExport(
			activeTab === 'essays' ? 'essays' : activeTab === 'surveys' ? 'surveys' : 'participants',
			[...selected],
			format
		);

	const fmtDate = (value: any) =>
		value ? new Date(value * 1000).toLocaleString() : 'Not available';
	const fmtDuration = (value: number | null | undefined) =>
		value === null || value === undefined ? 'Not available' : `${Math.round(value / 60)} min`;
	const fmtMsDuration = (value: number | null | undefined) =>
		value === null || value === undefined ? 'Not available' : `${Math.round(value / 1000)} sec`;
	const yesNo = (value: boolean) => (value ? 'Yes' : 'No');
	const distributions = (value: any) => Object.entries(value ?? {}) as [string, any[]][];

	onMount(async () => {
		for (const key of Object.keys(filters))
			filters[key] = $pageStore.url.searchParams.get(key) ?? '';
		try {
			availableFilters = await getResearchFilters(localStorage.token);
		} catch (message) {
			error = String(message);
		}
		mounted = true;
		scheduleLoad(0);
	});

	onDestroy(() => {
		clearTimeout(loadTimer);
		controller?.abort();
	});
</script>

<div class="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8">
	<div class="mb-6">
		<h1 class="text-2xl font-semibold text-gray-900 dark:text-white">Researcher Dashboard</h1>
		<p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
			Experiment progress, participant activity, essays, LLM usage, and survey results.
		</p>
	</div>

	<nav aria-label="Research dashboard sections" class="mb-4 flex gap-1 overflow-x-auto">
		{#each tabs as tab}
			<a
				href={`/admin/analytics/${tab.id}?${$pageStore.url.searchParams}`}
				class="min-w-fit rounded-lg px-3 py-2 text-sm font-medium {activeTab === tab.id
					? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900'
					: 'text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-850'}"
			>
				{tab.label}
			</a>
		{/each}
	</nav>

	<section
		class="mb-6 rounded-2xl border border-gray-100 bg-gray-50/70 p-3 dark:border-gray-850 dark:bg-gray-900/60"
	>
		<div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-6">
			<select
				bind:value={filters.group_id}
				on:change={filtersChanged}
				aria-label="Group filter"
				class="research-input"
			>
				<option value="">All groups</option>
				{#each availableFilters.groups ?? [] as group}<option value={group.id}>{group.name}</option
					>{/each}
			</select>
			<select
				bind:value={filters.topic_id}
				on:change={filtersChanged}
				aria-label="Topic filter"
				class="research-input"
			>
				<option value="">All topics</option>
				{#each availableFilters.topics ?? [] as topic}<option value={topic.id}>{topic.title}</option
					>{/each}
			</select>
			<select
				bind:value={filters.state}
				on:change={filtersChanged}
				aria-label="State filter"
				class="research-input"
			>
				<option value="">All states</option>
				{#each availableFilters.states ?? [] as state}<option value={state}>{state}</option>{/each}
			</select>
			<select
				bind:value={filters.completed}
				on:change={filtersChanged}
				aria-label="Completion filter"
				class="research-input"
			>
				<option value="">Any completion</option>
				<option value="true">Completed</option>
				<option value="false">Not completed</option>
			</select>
			<input
				bind:value={filters.date_from}
				on:change={filtersChanged}
				type="date"
				aria-label="From date"
				class="research-input"
			/>
			<input
				bind:value={filters.date_to}
				on:change={filtersChanged}
				type="date"
				aria-label="To date"
				class="research-input"
			/>
		</div>
		{#if ['participants', 'essays'].includes(activeTab)}
			<input
				bind:value={filters.search}
				on:input={filtersChanged}
				placeholder="Search participants, groups, topics, or state"
				class="research-input mt-2 w-full"
			/>
		{/if}
	</section>

	{#if loading}
		<div class="flex min-h-72 items-center justify-center"><Spinner /></div>
	{:else if error}
		<div
			class="rounded-2xl border border-red-200 bg-red-50 p-8 text-center dark:border-red-900 dark:bg-red-950/30"
		>
			<p class="text-sm text-red-700 dark:text-red-300">{error}</p>
			<button
				class="mt-4 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white"
				on:click={() => scheduleLoad(0)}>Retry</button
			>
		</div>
	{:else if activeTab === 'overview'}
		<MetricCards metrics={data?.metrics ?? {}} labels={metricLabels} />
		<div class="mt-4 grid gap-4 lg:grid-cols-2">
			<ResearchBars title="Completion funnel" items={data?.funnel ?? []} />
			<ResearchBars title="Sessions over time" items={data?.sessions_over_time ?? []} />
			<ResearchBars title="Session state distribution" items={data?.states ?? []} />
			<ResearchBars
				title="Average session duration over time (seconds)"
				items={data?.average_duration_over_time ?? []}
			/>
		</div>
	{:else if activeTab === 'participants'}
		<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
			<p class="text-sm text-gray-500">{data?.total ?? 0} assigned participant records</p>
			<div class="flex items-center gap-2">
				<label class="text-xs text-gray-500"
					><input type="checkbox" bind:checked={anonymized} /> Anonymized export</label
				>
				<button class="research-button" on:click={() => exportSelected('csv')}>Export CSV</button>
				<button class="research-button" on:click={() => exportSelected('json')}>Export JSON</button>
			</div>
		</div>
		<div class="research-table-wrap">
			<table class="research-table">
				<thead
					><tr
						><th>Select</th><th
							><button on:click={() => sortBy('participant_id')}>Participant</button></th
						><th><button on:click={() => sortBy('name')}>Identity</button></th><th
							><button on:click={() => sortBy('group_name')}>Group / topic</button></th
						><th><button on:click={() => sortBy('state')}>State</button></th><th>Milestones</th><th
							><button on:click={() => sortBy('session_start_time')}>Started</button></th
						><th><button on:click={() => sortBy('session_duration')}>Duration</button></th><th
							><button on:click={() => sortBy('prompts')}>Prompts</button></th
						><th><button on:click={() => sortBy('total_tokens')}>Tokens</button></th><th
							><button on:click={() => sortBy('essay_word_count')}>Essay words</button></th
						><th>Keystrokes</th><th>Pauses</th><th>Pasted chars</th><th>Tab leaves</th><th
							>Time away</th
						></tr
					></thead
				>
				<tbody>
					{#each data?.items ?? [] as row}
						<tr
							class:cursor-pointer={row.session_id}
							on:click={() => row.session_id && openDetail(row.session_id)}
						>
							<td on:click|stopPropagation
								><input
									type="checkbox"
									disabled={!row.session_id}
									checked={selected.has(row.session_id)}
									on:change={() => toggleSelected(row.session_id)}
								/></td
							>
							<td class="font-medium">{row.participant_id}</td><td
								>{row.name ?? row.username ?? 'Not available'}<br /><span
									class="text-xs text-gray-400"
									>{row.username ? `@${row.username} · ` : ''}{row.email ?? ''}</span
								></td
							>
							<td
								>{row.group_name ?? 'Not available'}<br /><span class="text-xs text-gray-400"
									>{row.topic_title ?? 'Not assigned'}</span
								></td
							><td>{row.state}</td>
							<td
								>C {yesNo(row.consented)} · Pre {yesNo(row.pre_survey_completed)} · Essay {yesNo(
									row.essay_submitted
								)} · Post {yesNo(row.post_survey_completed)}</td
							>
							<td>{fmtDate(row.session_start_time)}</td><td>{fmtDuration(row.session_duration)}</td
							><td>{row.prompts}</td><td>{row.total_tokens}</td><td
								>{row.essay_word_count ?? 'Not available'}</td
							><td>{row.total_keystrokes}</td><td>{row.pause_count}</td><td
								>{row.total_pasted_chars}</td
							><td>{row.tab_switch_count}</td><td>{fmtMsDuration(row.total_time_away_ms)}</td>
						</tr>
					{:else}<tr
							><td colspan="16" class="py-12 text-center text-gray-400"
								>No participants match these filters.</td
							></tr
						>{/each}
				</tbody>
			</table>
		</div>
		<svelte:component
			this={Pagination}
			total={data?.total ?? 0}
			{page}
			{limit}
			onPage={changePage}
		/>
	{:else if activeTab === 'essays'}
		<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
			<p class="text-sm text-gray-500">{data?.total ?? 0} submitted essays</p>
			<div class="flex items-center gap-2">
				<label class="text-xs text-gray-500"
					><input type="checkbox" bind:checked={anonymized} /> Anonymized export</label
				><button class="research-button" on:click={() => exportSelected('csv')}>Export CSV</button
				><button class="research-button" on:click={() => exportSelected('json')}>Export JSON</button
				>
			</div>
		</div>
		<div class="research-table-wrap">
			<table class="research-table">
				<thead
					><tr
						><th>Select</th><th
							><button on:click={() => sortBy('participant_id')}>Participant</button></th
						><th><button on:click={() => sortBy('name')}>Identity</button></th><th
							><button on:click={() => sortBy('group_name')}>Group</button></th
						><th><button on:click={() => sortBy('topic_title')}>Topic</button></th><th
							><button on:click={() => sortBy('state')}>State</button></th
						><th><button on:click={() => sortBy('word_count')}>Words</button></th><th
							><button on:click={() => sortBy('character_count')}>Characters</button></th
						><th><button on:click={() => sortBy('submitted_at')}>Submitted</button></th></tr
					></thead
				><tbody>
					{#each data?.items ?? [] as row}<tr
							><td
								><input
									type="checkbox"
									checked={selected.has(row.essay_id)}
									on:change={() => toggleSelected(row.essay_id)}
								/></td
							><td>{row.participant_id}</td><td>{row.name ?? row.username ?? 'Not available'}</td
							><td>{row.group_name ?? 'Not available'}</td><td
								>{row.topic_title ?? 'Not available'}</td
							><td>{row.state}</td><td>{row.word_count ?? 'Not available'}</td><td
								>{row.character_count ?? 'Not available'}</td
							><td>{fmtDate(row.submitted_at)}</td></tr
						>
					{:else}<tr
							><td colspan="9" class="py-12 text-center text-gray-400"
								>No submitted essays match these filters.</td
							></tr
						>{/each}
				</tbody>
			</table>
		</div>
		<svelte:component
			this={Pagination}
			total={data?.total ?? 0}
			{page}
			{limit}
			onPage={changePage}
		/>
	{:else if activeTab === 'usage'}
		<MetricCards metrics={data?.metrics ?? {}} labels={metricLabels} />
		<div class="mt-4 grid gap-4 lg:grid-cols-2">
			<ResearchBars
				title="Prompt count distribution"
				items={data?.prompt_distribution ?? []}
			/><ResearchBars
				title="Token usage distribution"
				items={data?.token_distribution ?? []}
			/><ResearchBars
				title="Token usage by group"
				items={(data?.by_group ?? []).map((x: any) => ({ label: x.label, value: x.tokens }))}
			/><ResearchBars
				title="Token usage by topic"
				items={(data?.by_topic ?? []).map((x: any) => ({ label: x.label, value: x.tokens }))}
			/>
		</div>
		<h2 class="mb-2 mt-6 text-lg font-semibold">Top participants by token usage</h2>
		<div class="research-table-wrap">
			<table class="research-table">
				<thead
					><tr
						><th>Participant</th><th>Identity</th><th>Prompts</th><th>Responses</th><th
							>Input tokens</th
						><th>Output tokens</th><th>Total tokens</th></tr
					></thead
				><tbody
					>{#each data?.top_participants ?? [] as row}<tr
							><td>{row.participant_id}</td><td>{row.name ?? 'Not available'}</td><td
								>{row.prompts}</td
							><td>{row.responses}</td><td>{row.input_tokens}</td><td>{row.output_tokens}</td><td
								>{row.total_tokens}</td
							></tr
						>{:else}<tr
							><td colspan="7" class="py-12 text-center text-gray-400"
								>No LLM usage recorded yet.</td
							></tr
						>{/each}</tbody
				>
			</table>
		</div>
	{:else if activeTab === 'essay-stats'}
		<MetricCards metrics={data?.metrics ?? {}} labels={metricLabels} />
		<div class="mt-4 grid gap-4 lg:grid-cols-2">
			<ResearchBars title="Word count distribution" items={data?.distribution ?? []} /><ResearchBars
				title="Average words by group"
				items={data?.by_group ?? []}
			/><ResearchBars title="Average words by topic" items={data?.by_topic ?? []} /><ResearchBars
				title="Average words by AI usage level"
				items={data?.by_ai_usage ?? []}
			/>
		</div>
		<h2 class="mb-2 mt-6 text-lg font-semibold">
			Writing comparisons <span class="text-sm font-normal text-gray-400"
				>(capped at 200 sessions)</span
			>
		</h2>
		<div class="research-table-wrap">
			<table class="research-table">
				<thead
					><tr
						><th>Session</th><th>Prompts</th><th>Tokens</th><th>Writing duration</th><th
							>Word count</th
						></tr
					></thead
				><tbody
					>{#each data?.comparisons ?? [] as row}<tr
							><td>{row.session_id}</td><td>{row.prompts}</td><td>{row.tokens}</td><td
								>{fmtDuration(row.duration)}</td
							><td>{row.word_count}</td></tr
						>{:else}<tr
							><td colspan="5" class="py-12 text-center text-gray-400"
								>No essay statistics available yet.</td
							></tr
						>{/each}</tbody
				>
			</table>
		</div>
	{:else if activeTab === 'surveys'}
		<div class="grid gap-4 lg:grid-cols-2">
			{#each distributions(data?.pre) as [name, items]}<ResearchBars
					title={`Pre-survey: ${name.replaceAll('_', ' ')}`}
					{items}
					valueKey="count"
				/>{/each}
			{#each distributions(data?.post) as [name, items]}<ResearchBars
					title={`Post-survey: ${name.replaceAll('_', ' ')}`}
					{items}
					valueKey="count"
				/>{/each}
		</div>
		<h2 class="mb-2 mt-6 text-lg font-semibold">Survey responses and optional comments</h2>
		<div class="research-table-wrap">
			<table class="research-table">
				<thead
					><tr
						><th>Select</th><th>Participant</th><th>State</th><th>Pre-survey</th><th>Post-survey</th
						><th>Optional comment</th></tr
					></thead
				><tbody
					>{#each data?.responses?.items ?? [] as row}<tr
							><td
								><input
									type="checkbox"
									checked={selected.has(row.session_id)}
									on:change={() => toggleSelected(row.session_id)}
								/></td
							><td>{row.participant_id}</td><td>{row.state}</td><td
								>{yesNo(row.pre_survey_completed)}</td
							><td>{yesNo(row.post_survey_completed)}</td><td class="max-w-3xl whitespace-normal"
								>{row.comments ?? 'Not available'}</td
							></tr
						>{:else}<tr
							><td colspan="6" class="py-12 text-center text-gray-400"
								>No survey responses match these filters.</td
							></tr
						>{/each}</tbody
				>
			</table>
		</div>
		<svelte:component
			this={Pagination}
			total={data?.responses?.total ?? 0}
			{page}
			{limit}
			onPage={changePage}
		/>
		<div class="mt-3 flex justify-end gap-2">
			<label class="text-xs text-gray-500"
				><input type="checkbox" bind:checked={anonymized} /> Anonymized export</label
			><button class="research-button" on:click={() => exportSelected('csv')}
				>Export selected CSV</button
			><button class="research-button" on:click={() => exportSelected('json')}
				>Export selected JSON</button
			>
		</div>
	{/if}
</div>

{#if detailLoading || detail}
	<div class="fixed inset-0 z-50 flex justify-end">
		<button
			class="absolute inset-0 bg-black/30"
			aria-label="Close session detail"
			on:click={() => (detail = null)}
		></button>
		<aside
			class="relative h-full w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-2xl dark:bg-gray-900"
			aria-label="Session detail"
		>
			<div class="flex items-center justify-between">
				<h2 class="text-xl font-semibold">Session detail</h2>
				<button class="research-button" on:click={() => (detail = null)}>Close</button>
			</div>
			{#if detailLoading}<div class="flex h-64 items-center justify-center"><Spinner /></div>
			{:else}
				<p class="mt-2 text-sm text-gray-500">
					{detail.participant_id} · {detail.name ?? 'Not available'} · {detail.state}
				</p>
				<div class="mt-3 flex gap-2">
					<button
						class="research-button"
						on:click={() => runExport('participants', [detail.session_id], 'csv')}
						>Export session CSV</button
					><button
						class="research-button"
						on:click={() => runExport('participants', [detail.session_id], 'json')}
						>Export session JSON</button
					>
				</div>
				<h3 class="mb-2 mt-6 font-semibold">Timeline</h3>
				<div class="grid gap-2 sm:grid-cols-2">
					{#each Object.entries(detail.timeline ?? {}) as [name, value]}<div
							class="rounded-xl bg-gray-50 p-3 dark:bg-gray-850"
						>
							<div class="text-xs text-gray-400">{name.replaceAll('_', ' ')}</div>
							<div class="mt-1 text-sm">{fmtDate(value)}</div>
						</div>{/each}
				</div>
				<h3 class="mb-2 mt-6 font-semibold">Topic and essay</h3>
				<p class="text-sm">
					<strong>{detail.topic_title ?? 'Not available'}</strong><br />{detail.topic_question ??
						'Not available'}
				</p>
				<p class="mt-3 text-sm">
					Words: {detail.essay?.word_count ?? 'Not available'} · Characters: {detail.essay
						?.character_count ?? 'Not available'} · Submitted: {fmtDate(detail.essay?.submitted_at)}
				</p>
				<h3 class="mb-2 mt-6 font-semibold">LLM usage and durations</h3>
				<p class="text-sm">
					Prompts: {detail.prompts} · Responses: {detail.responses} · Input tokens: {detail.input_tokens}
					· Output tokens: {detail.output_tokens} · Total tokens: {detail.total_tokens}
				</p>
				<p class="mt-2 text-sm">
					Session: {fmtDuration(detail.session_duration)} · Writing: {fmtDuration(
						detail.writing_duration
					)} · Post-survey: {fmtDuration(detail.post_survey_duration)}
				</p>
				<h3 class="mb-2 mt-6 font-semibold">Interaction telemetry</h3>
				<div class="grid gap-2 sm:grid-cols-2">
					{#each Object.entries(detail.telemetry_summary ?? {}) as [name, value]}<div
							class="rounded-xl bg-gray-50 p-3 dark:bg-gray-850"
						>
							<div class="text-xs text-gray-400">{name.replaceAll('_', ' ')}</div>
							<div class="mt-1 text-sm">{value ?? 'Not available'}</div>
						</div>{/each}
				</div>
				<h3 class="mb-2 mt-6 font-semibold">Pre-survey</h3>
				<pre class="research-json">{JSON.stringify(
						detail.pre_survey ?? 'Not available',
						null,
						2
					)}</pre>
				<h3 class="mb-2 mt-6 font-semibold">Post-survey</h3>
				<pre class="research-json">{JSON.stringify(
						detail.post_survey ?? 'Not available',
						null,
						2
					)}</pre>
			{/if}
		</aside>
	</div>
{/if}

<style>
	:global(.research-input) {
		border: 1px solid rgb(229 231 235);
		border-radius: 0.65rem;
		background: white;
		padding: 0.55rem 0.7rem;
		font-size: 0.8rem;
		outline: none;
	}
	:global(.dark .research-input) {
		border-color: rgb(55 65 81);
		background: rgb(17 24 39);
	}
	:global(.research-button) {
		border-radius: 0.6rem;
		background: rgb(243 244 246);
		padding: 0.45rem 0.7rem;
		font-size: 0.75rem;
		font-weight: 500;
	}
	:global(.dark .research-button) {
		background: rgb(31 41 55);
	}
	.research-table-wrap {
		overflow-x: auto;
		border: 1px solid rgb(243 244 246);
		border-radius: 1rem;
	}
	:global(.dark .research-table-wrap) {
		border-color: rgb(31 41 55);
	}
	.research-table {
		width: 100%;
		min-width: 900px;
		border-collapse: collapse;
		font-size: 0.75rem;
	}
	.research-table th {
		padding: 0.7rem;
		text-align: left;
		color: rgb(107 114 128);
		font-weight: 600;
		background: rgb(249 250 251);
	}
	:global(.dark .research-table th) {
		background: rgb(17 24 39);
	}
	.research-table td {
		border-top: 1px solid rgb(243 244 246);
		padding: 0.7rem;
		white-space: nowrap;
	}
	:global(.dark .research-table td) {
		border-color: rgb(31 41 55);
	}
	.research-table tr:hover td {
		background: rgb(249 250 251);
	}
	:global(.dark .research-table tr:hover td) {
		background: rgb(17 24 39);
	}
	.research-json {
		overflow-x: auto;
		border-radius: 0.75rem;
		background: rgb(249 250 251);
		padding: 0.75rem;
		font-size: 0.7rem;
		white-space: pre-wrap;
	}
	:global(.dark .research-json) {
		background: rgb(17 24 39);
	}
</style>
