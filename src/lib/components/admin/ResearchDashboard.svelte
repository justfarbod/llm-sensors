<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { goto } from '$app/navigation';
	import { page as pageStore } from '$app/stores';
	import { toast } from 'svelte-sonner';

	import {
		exportFullResearchSessions,
		getQuestionSubmission,
		getResearchFilters,
		getResearchSection,
		getResearchSession,
		getResearchTabActivity,
		overrideQuestionScore,
		retryQuestionGrading
	} from '$lib/apis/experiment-analytics';
	import MetricCards from './ResearchDashboard/MetricCards.svelte';
	import ResearchBars from './ResearchDashboard/ResearchBars.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Pagination from './ResearchDashboard/Pagination.svelte';
	import { completedRetryScore } from '$lib/utils/questionGrading';

	const tabs = [
		{ id: 'overview', label: 'Overview' },
		{ id: 'participants', label: 'Participants' },
		{ id: 'essays', label: 'Essays' },
		{ id: 'question-results', label: 'Question Results' },
		{ id: 'usage', label: 'LLM Usage' },
		{ id: 'essay-stats', label: 'Essay Statistics' },
		{ id: 'surveys', label: 'Survey Results' }
	];
	const i18n: Writable<i18nType> = getContext('i18n');
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
	let detailSessionId = '';
	let detailPollTimer: ReturnType<typeof setTimeout>;
	let tabActivity: any = null;
	let tabActivityLoading = false;
	let tabEventType = '';
	let tabBrowserId = '';
	let tabWindowId = '';
	let tabDateFrom = '';
	let tabDateTo = '';
	let tabActivityPage = 1;
	let questionDetail: any = null;
	let questionDetailLoading = false;
	let scoreDrafts: Record<string, number> = {};
	let scoreNotes: Record<string, string> = {};
	let gradingRetryAttempts: Record<string, string> = {};
	let loadTimer: ReturnType<typeof setTimeout>;
	let gradingPollTimer: ReturnType<typeof setTimeout>;
	let gradingPollController: AbortController | null = null;
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
		search: ['participants', 'essays', 'question-results'].includes(activeTab)
			? filters.search
			: '',
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

	const refreshDetail = async (sessionId: string, initial = false) => {
		clearTimeout(detailPollTimer);
		if (initial) detailLoading = true;
		try {
			const next = await getResearchSession(localStorage.token, sessionId);
			if (detailSessionId !== sessionId) return;
			detail = next;
			if (next.state === 'IN_PROGRESS')
				detailPollTimer = setTimeout(() => refreshDetail(sessionId), 3000);
		} catch (message) {
			if (initial) toast.error(String(message));
		} finally {
			if (initial) detailLoading = false;
		}
	};

	const openDetail = async (sessionId: string) => {
		clearTimeout(detailPollTimer);
		detailSessionId = sessionId;
		detail = null;
		tabActivity = null;
		tabActivityPage = 1;
		tabEventType = '';
		tabBrowserId = '';
		tabWindowId = '';
		tabDateFrom = '';
		tabDateTo = '';
		await refreshDetail(sessionId, true);
	};

	const closeDetail = () => {
		clearTimeout(detailPollTimer);
		detailSessionId = '';
		detail = null;
	};

	const loadTabActivity = async (sessionId: string, pageValue = tabActivityPage) => {
		tabActivityLoading = true;
		try {
			tabActivity = await getResearchTabActivity(localStorage.token, sessionId, {
				page: pageValue,
				limit: 100,
				event_type: tabEventType,
				browser_tab_id: tabBrowserId,
				browser_window_id: tabWindowId,
				date_from: tabDateFrom ? Math.floor(new Date(tabDateFrom).getTime() / 1000) : '',
				date_to: tabDateTo ? Math.floor(new Date(tabDateTo).getTime() / 1000) : ''
			});
			tabActivityPage = pageValue;
		} catch (message) {
			toast.error(String(message));
		} finally {
			tabActivityLoading = false;
		}
	};

	const openQuestionDetail = async (submissionId: string) => {
		clearTimeout(gradingPollTimer);
		gradingPollController?.abort();
		questionDetail = null;
		questionDetailLoading = true;
		try {
			questionDetail = await getQuestionSubmission(localStorage.token, submissionId);
			scoreDrafts = Object.fromEntries(
				(questionDetail.responses ?? []).map((response: any) => [
					response.id,
					response.effective_score ?? response.generated_score ?? 0
				])
			);
			scoreNotes = {};
			gradingRetryAttempts = {};
			if (gradingInFlight(questionDetail)) pollQuestionDetail(submissionId);
		} catch (message) {
			toast.error(String(message));
		} finally {
			questionDetailLoading = false;
		}
	};

	const gradingInFlight = (submission: any) =>
		(submission?.responses ?? []).some(
			(response: any) =>
				['PENDING', 'RUNNING'].includes(response.grading_status) ||
				(response.attempts ?? []).some((attempt: any) =>
					['PENDING', 'RUNNING'].includes(attempt.status)
				)
		);

	const applyCompletedRetryScores = (submission: any) => {
		const nextDrafts = { ...scoreDrafts };
		const nextAttempts = { ...gradingRetryAttempts };
		for (const response of submission?.responses ?? []) {
			const attemptId = nextAttempts[response.id];
			if (!attemptId) {
				if (nextDrafts[response.id] === undefined)
					nextDrafts[response.id] = response.effective_score ?? response.generated_score ?? 0;
				continue;
			}
			const update = completedRetryScore(response, attemptId);
			if (!update.terminal) continue;
			if (update.score !== undefined) nextDrafts[response.id] = update.score;
			delete nextAttempts[response.id];
		}
		scoreDrafts = nextDrafts;
		gradingRetryAttempts = nextAttempts;
	};

	const pollQuestionDetail = (submissionId: string, delay = 1000) => {
		clearTimeout(gradingPollTimer);
		gradingPollTimer = setTimeout(async () => {
			gradingPollController?.abort();
			gradingPollController = new AbortController();
			try {
				const next = await getQuestionSubmission(
					localStorage.token,
					submissionId,
					gradingPollController.signal
				);
				if (questionDetail?.submission_id !== submissionId) return;
				questionDetail = next;
				applyCompletedRetryScores(next);
				if (gradingInFlight(next)) pollQuestionDetail(submissionId, Math.min(5000, delay + 1000));
				else await load();
			} catch (message) {
				if (!gradingPollController?.signal.aborted) toast.error(String(message));
			}
		}, delay);
	};

	const closeQuestionDetail = () => {
		clearTimeout(gradingPollTimer);
		gradingPollController?.abort();
		questionDetail = null;
		gradingRetryAttempts = {};
	};

	const saveQuestionScore = async (responseId: string) => {
		try {
			questionDetail = await overrideQuestionScore(
				localStorage.token,
				responseId,
				scoreDrafts[responseId],
				scoreNotes[responseId]
			);
			scoreDrafts = {
				...scoreDrafts,
				[responseId]:
					questionDetail.responses?.find((response: any) => response.id === responseId)
						?.effective_score ?? scoreDrafts[responseId]
			};
			toast.success($i18n.t('Score saved'));
			await load();
		} catch (message) {
			toast.error(String(message));
		}
	};

	const retryGrading = async (responseId: string) => {
		try {
			const result = await retryQuestionGrading(localStorage.token, responseId);
			if (result.attempt_id)
				gradingRetryAttempts = {
					...gradingRetryAttempts,
					[responseId]: result.attempt_id
				};
			if (result.submission) questionDetail = result.submission;
			if (result.submission) applyCompletedRetryScores(result.submission);
			toast.success($i18n.t('Grading retry queued'));
			if (questionDetail?.submission_id) pollQuestionDetail(questionDetail.submission_id);
		} catch (message) {
			toast.error(String(message));
		}
	};

	const runFullSessionExport = async (ids: string[]) => {
		if (!ids.length) return toast.info('Select at least one session to export.');
		try {
			const { blob, filename } = await exportFullResearchSessions(
				localStorage.token,
				ids,
				anonymized
			);
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = filename;
			document.body.appendChild(link);
			link.click();
			link.remove();
			setTimeout(() => URL.revokeObjectURL(url), 0);
		} catch (message) {
			toast.error(String(message));
		}
	};

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
		clearTimeout(detailPollTimer);
		clearTimeout(gradingPollTimer);
		controller?.abort();
		gradingPollController?.abort();
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
				{$i18n.t(tab.label)}
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
		{#if ['participants', 'essays', 'question-results'].includes(activeTab)}
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
			<div class="flex flex-wrap items-center justify-end gap-2">
				<label
					class="text-xs text-gray-500"
					title="Free-form text, chat content, and telemetry URLs are not redacted."
					><input type="checkbox" bind:checked={anonymized} /> Anonymize account identifiers</label
				>
				<button
					class="research-button"
					disabled={selected.size === 0}
					on:click={() => runFullSessionExport([...selected])}
					>Export full sessions (JSON){selected.size ? ` (${selected.size})` : ''}</button
				>
				<p class="w-full text-right text-xs text-amber-700 dark:text-amber-300">
					Essays, chats, survey text, tab titles, and URLs remain unchanged.
				</p>
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
		</div>
		<div class="research-table-wrap">
			<table class="research-table">
				<thead
					><tr
						><th><button on:click={() => sortBy('participant_id')}>Participant</button></th><th
							><button on:click={() => sortBy('name')}>Identity</button></th
						><th><button on:click={() => sortBy('group_name')}>Group</button></th><th
							><button on:click={() => sortBy('topic_title')}>Topic</button></th
						><th><button on:click={() => sortBy('state')}>State</button></th><th
							><button on:click={() => sortBy('word_count')}>Words</button></th
						><th><button on:click={() => sortBy('character_count')}>Characters</button></th><th
							><button on:click={() => sortBy('submitted_at')}>Submitted</button></th
						></tr
					></thead
				><tbody>
					{#each data?.items ?? [] as row}<tr
							><td>{row.participant_id}</td><td>{row.name ?? row.username ?? 'Not available'}</td
							><td>{row.group_name ?? 'Not available'}</td><td
								>{row.topic_title ?? 'Not available'}</td
							><td>{row.state}</td><td>{row.word_count ?? 'Not available'}</td><td
								>{row.character_count ?? 'Not available'}</td
							><td>{fmtDate(row.submitted_at)}</td></tr
						>
					{:else}<tr
							><td colspan="8" class="py-12 text-center text-gray-400"
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
	{:else if activeTab === 'question-results'}
		<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
			<p class="text-sm text-gray-500">{data?.total ?? 0} {$i18n.t('question task submissions')}</p>
			<p class="text-xs text-gray-400">
				{$i18n.t('Open a submission to review answers, grading attempts, and score history.')}
			</p>
		</div>
		<div class="research-table-wrap">
			<table class="research-table">
				<thead
					><tr
						><th>{$i18n.t('Participant')}</th><th>{$i18n.t('Group')}</th><th>{$i18n.t('Task')}</th
						><th>{$i18n.t('Status')}</th><th>{$i18n.t('Grading')}</th><th>{$i18n.t('Score')}</th><th
							>{$i18n.t('Submitted')}</th
						></tr
					></thead
				>
				<tbody>
					{#each data?.items ?? [] as row}
						<tr class="cursor-pointer" on:click={() => openQuestionDetail(row.submission_id)}>
							<td
								>{row.participant_id}<br /><span class="text-xs text-gray-400"
									>{row.name ?? ''}</span
								></td
							>
							<td>{row.group_name}</td><td>{row.task_title}</td><td>{row.status}</td><td
								>{row.grading_status}</td
							>
							<td>{row.score} / {row.maximum_score}</td><td>{fmtDate(row.submitted_at)}</td>
						</tr>
					{:else}
						<tr
							><td colspan="7" class="py-12 text-center text-gray-400"
								>{$i18n.t('No question submissions match these filters.')}</td
							></tr
						>
					{/each}
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
		{#if data?.dynamic?.length}<div class="mb-6 space-y-5">
				{#each data.dynamic as survey}<section
						class="rounded-2xl border border-gray-100 p-4 dark:border-gray-800"
					>
						<div class="flex flex-wrap items-center justify-between gap-2">
							<h2 class="text-lg font-semibold">{survey.title} · v{survey.version}</h2>
							<span class="text-xs text-gray-500"
								>{survey.submission_count}
								{$i18n.t('submitted')} · {survey.skipped_count}
								{$i18n.t('skipped')}</span
							>
						</div>
						<div class="mt-4 grid gap-4 lg:grid-cols-2">
							{#each survey.questions as question}<ResearchBars
									title={question.prompt}
									items={question.distribution}
									valueKey="count"
								/>{/each}
						</div>
					</section>{/each}
			</div>{/if}
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
						><th>Participant</th><th>State</th><th>Pre-survey</th><th>Post-survey</th><th
							>{$i18n.t('Pipeline surveys')}</th
						><th>Optional comment</th></tr
					></thead
				><tbody
					>{#each data?.responses?.items ?? [] as row}<tr
							><td>{row.participant_id}</td><td>{row.state}</td><td
								>{yesNo(row.pre_survey_completed)}</td
							><td>{yesNo(row.post_survey_completed)}</td><td class="max-w-3xl whitespace-normal"
								>{#each row.survey_submissions ?? [] as survey}<div>
										{survey.title} · {$i18n.t(survey.status)}
									</div>{:else}—{/each}</td
							><td class="max-w-3xl whitespace-normal">{row.comments ?? 'Not available'}</td></tr
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
	{/if}
</div>

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
				{#if detail.essay_tasks?.length}
					<div class="space-y-3">
						{#each detail.essay_tasks as essayTask}
							<div class="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
								<div class="flex flex-wrap items-center justify-between gap-2">
									<strong>{essayTask.title}</strong>
									<span class="text-xs text-gray-500">{essayTask.status}</span>
								</div>
								<p class="mt-2 text-sm">
									<strong>{essayTask.topic_title ?? 'Topic not available'}</strong><br
									/>{essayTask.topic_question ?? 'Topic question not available'}
								</p>
								<p class="mt-3 text-xs text-gray-500">
									{essayTask.is_draft ? 'Current draft' : 'Submitted essay'} · Words: {essayTask.word_count}
									· Characters: {essayTask.character_count} · Updated: {fmtDate(
										essayTask.updated_at
									)}
								</p>
								<div
									class="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-sm leading-6 dark:bg-gray-850"
								>
									{essayTask.content || 'No essay text has been entered yet.'}
								</div>
							</div>
						{/each}
					</div>
				{:else}
					<p class="text-sm">
						<strong>{detail.topic_title ?? 'Not available'}</strong><br />{detail.topic_question ??
							'Not available'}
					</p>
					<p class="mt-3 text-sm">
						Words: {detail.essay?.word_count ?? 'Not available'} · Characters: {detail.essay
							?.character_count ?? 'Not available'} · Submitted: {fmtDate(
							detail.essay?.submitted_at
						)}
					</p>
					<div
						class="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-sm leading-6 dark:bg-gray-850"
					>
						{detail.essay?.content || 'No essay text has been entered yet.'}
					</div>
				{/if}
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

{#if questionDetailLoading || questionDetail}
	<div class="fixed inset-0 z-50 flex justify-end">
		<button
			class="absolute inset-0 bg-black/30"
			aria-label={$i18n.t('Close question submission detail')}
			on:click={closeQuestionDetail}
		></button>
		<aside
			class="relative h-full w-full max-w-3xl overflow-y-auto bg-white p-6 shadow-2xl dark:bg-gray-900"
		>
			<div class="flex items-center justify-between gap-3">
				<div>
					<h2 class="text-xl font-semibold">{$i18n.t('Question submission')}</h2>
					{#if questionDetail}<p class="mt-1 text-sm text-gray-500">
							{questionDetail.task.title} · {questionDetail.participant_id} · {questionDetail.group
								.name}
						</p>{/if}
				</div>
				<button class="research-button" on:click={closeQuestionDetail}>{$i18n.t('Close')}</button>
			</div>
			{#if questionDetailLoading}
				<div class="flex h-64 items-center justify-center"><Spinner /></div>
			{:else}
				<div class="mt-4 rounded-xl bg-gray-50 p-3 text-sm dark:bg-gray-850">
					{$i18n.t('Total score')}:
					<strong
						>{questionDetail.score ?? $i18n.t('Pending')} / {questionDetail.maximum_score}</strong
					>
					{#if questionDetail.score === null}<span class="ml-3 text-xs text-gray-500"
							>{$i18n.t('Provisional')}: {questionDetail.provisional_score} / {questionDetail.maximum_score}</span
						>{/if}
					<span class="ml-3 text-xs text-gray-500">{questionDetail.grading_status}</span>
				</div>
				<div class="mt-4 space-y-4">
					{#each questionDetail.responses ?? [] as response, responseIndex}
						<section class="rounded-2xl border border-gray-100 p-4 dark:border-gray-800">
							{#if response.grading_status === 'FAILED'}
								{@const failedAttempt = [...(response.attempts ?? [])]
									.reverse()
									.find((attempt: any) => attempt.status === 'FAILED')}
								<div
									class="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/20 dark:text-red-200"
								>
									<strong>{$i18n.t('Model scoring problem')}</strong>
									<div class="mt-1 text-xs">
										{$i18n.t(
											'This response has no final score. Retry grading or enter an administrative score.'
										)}
									</div>
									{#if failedAttempt}<div class="mt-1 text-xs">
											{failedAttempt.model_id ?? $i18n.t('Model unavailable')} · {failedAttempt.error_code ??
												$i18n.t('Provider error')}
										</div>{/if}
								</div>{/if}
							<div class="flex flex-wrap items-start justify-between gap-2">
								<div>
									<h3 class="font-medium">{responseIndex + 1}. {response.question.title}</h3>
									<p class="mt-1 whitespace-pre-wrap text-sm text-gray-500">
										{response.question.description}
									</p>
								</div>
								<span class="rounded-full bg-gray-100 px-2 py-1 text-xs dark:bg-gray-800"
									>{response.grading_status}</span
								>
							</div>

							<div class="mt-3 grid gap-3 sm:grid-cols-2">
								<div class="rounded-xl bg-gray-50 p-3 text-sm dark:bg-gray-850">
									<div class="mb-2 text-xs font-semibold uppercase text-gray-400">
										{$i18n.t('Participant answer')}
									</div>
									{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(response.question.question_type)}
										<ul class="space-y-1">
											{#each response.question.choices.filter( (choice: any) => response.selected_choice_ids.includes(choice.id) ) as choice}<li
												>
													{choice.text}
												</li>{:else}<li class="text-gray-400">{$i18n.t('No answer')}</li>{/each}
										</ul>
									{:else if response.question.question_type === 'FILL_BLANK'}
										<ul class="space-y-1">
											{#each response.blank_answers as answer}<li>
													<strong
														>{response.question.blanks.find(
															(blank: any) => blank.id === answer.blank_id
														)?.key}:</strong
													>
													{answer.answer || $i18n.t('No answer')}
												</li>{/each}
										</ul>
									{:else}
										<p class="whitespace-pre-wrap">
											{response.text_answer || $i18n.t('No answer')}
										</p>
									{/if}
								</div>
								<div class="rounded-xl bg-emerald-50 p-3 text-sm dark:bg-emerald-950/20">
									<div
										class="mb-2 text-xs font-semibold uppercase text-emerald-700 dark:text-emerald-300"
									>
										{$i18n.t('Answer key')}
									</div>
									{#if ['SINGLE_CHOICE', 'MULTIPLE_SELECT'].includes(response.question.question_type)}
										<ul class="space-y-1">
											{#each response.question.choices.filter((choice: any) => choice.is_correct) as choice}<li
												>
													{choice.text}
												</li>{/each}
										</ul>
									{:else if response.question.question_type === 'FILL_BLANK'}
										<ul class="space-y-1">
											{#each response.question.blanks as blank}<li>
													<strong>{blank.key}:</strong>
													{blank.accepted_answers.join(', ')}
												</li>{/each}
										</ul>
									{:else}
										<p class="whitespace-pre-wrap">
											{response.question.expected_answer || $i18n.t('Manual review')}
										</p>
									{/if}
								</div>
							</div>

							{#if response.rationale}<p
									class="mt-3 rounded-xl bg-blue-50 p-3 text-sm dark:bg-blue-950/20"
								>
									<strong>{$i18n.t('Grading rationale')}:</strong>
									{response.rationale}
								</p>{/if}
							<div class="mt-3 flex flex-wrap items-end gap-2">
								<label class="text-xs text-gray-500"
									>{$i18n.t('Score')}<input
										class="research-input ml-2 !w-24"
										type="number"
										min="0"
										max={response.question.max_score}
										step="0.01"
										value={scoreDrafts[response.id] ?? 0}
										on:input={(event) =>
											(scoreDrafts = {
												...scoreDrafts,
												[response.id]: Number(event.currentTarget.value)
											})}
									/></label
								>
								<input
									class="research-input min-w-52 flex-1"
									value={scoreNotes[response.id] ?? ''}
									on:input={(event) => (scoreNotes[response.id] = event.currentTarget.value)}
									placeholder={$i18n.t('Optional override note')}
								/>
								<button class="research-button" on:click={() => saveQuestionScore(response.id)}
									>{$i18n.t('Save score')}</button
								>
								{#if response.grading_method === 'LLM_ASSISTED' || response.attempts?.some((attempt: any) => attempt.method === 'LLM_ASSISTED')}<button
										class="research-button"
										disabled={['PENDING', 'RUNNING'].includes(response.grading_status)}
										on:click={() => retryGrading(response.id)}
										>{$i18n.t('Retry LLM grading')}</button
									>{/if}
							</div>

							{#if response.attempts?.length || response.overrides?.length}
								<details class="mt-3 text-xs">
									<summary class="cursor-pointer font-medium">{$i18n.t('Grading history')}</summary>
									<div class="mt-2 space-y-2">
										{#each response.attempts ?? [] as attempt}<div
												class="rounded-lg bg-gray-50 p-2 dark:bg-gray-850"
											>
												{$i18n.t('Attempt')} · {attempt.status} · {attempt.model_id ??
													attempt.method} · {attempt.awarded_score ?? '—'}{attempt.error_code
													? ` · ${attempt.error_code}`
													: ''}
											</div>{/each}
										{#each response.overrides ?? [] as override}<div
												class="rounded-lg bg-gray-50 p-2 dark:bg-gray-850"
											>
												{$i18n.t('Override')} · {override.previous_score ?? '—'} → {override.new_score}{override.note
													? ` · ${override.note}`
													: ''}
											</div>{/each}
									</div>
								</details>
							{/if}
						</section>
					{/each}
				</div>
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
	:global(.research-button:disabled) {
		cursor: not-allowed;
		opacity: 0.5;
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
