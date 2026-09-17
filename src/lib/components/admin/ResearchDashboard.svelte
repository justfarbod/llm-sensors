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
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SessionDetail from './ResearchDashboard/SessionDetail.svelte';
	import GradingDetail from './ResearchDashboard/GradingDetail.svelte';
	import Overview from './ResearchDashboard/Overview.svelte';
	import Workflows from './ResearchDashboard/Workflows.svelte';
	import Participants from './ResearchDashboard/Participants.svelte';
	import Usage from './ResearchDashboard/Usage.svelte';
	import { completedRetryScore } from '$lib/utils/questionGrading';

	const tabs = [
		{ id: 'overview', label: 'Overview' },
		{ id: 'participants', label: 'Participants' },
		{ id: 'workflows', label: 'Workflows' },
		{ id: 'usage', label: 'LLM Usage' }
	];
	const i18n: Writable<i18nType> = getContext('i18n');

	let mounted = false;
	let activeTab = 'overview';
	let loadedTab = '';
	let availableFilters: any = { groups: [], topics: [], states: [] };
	let filters: Record<string, string> = {
		workflow_id: '',
		configuration_id: '',
		condition_key: '',
		data_kind: '',
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
		: ['essays', 'question-results', 'essay-stats', 'surveys'].includes($pageStore.params.tab ?? '')
			? 'workflows'
			: 'overview';
	$: if (
		mounted &&
		['essays', 'question-results', 'essay-stats', 'surveys'].includes($pageStore.params.tab ?? '')
	) {
		goto(`/admin/analytics/workflows${$pageStore.url.search}`, { replaceState: true });
	}
	$: if (mounted && activeTab !== loadedTab) {
		page = 1;
		selected = new Set();
		scheduleLoad(0);
	}

	const epoch = (date: string, end = false) =>
		date ? Math.floor(new Date(`${date}T${end ? '23:59:59' : '00:00:00'}`).getTime() / 1000) : '';

	const requestFilters = () => ({
		workflow_id: filters.workflow_id,
		configuration_id: filters.configuration_id,
		condition_key: filters.condition_key,
		data_kind: filters.data_kind,
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
		const currentController = new AbortController();
		controller = currentController;
		loading = true;
		error = '';
		try {
			data = await getResearchSection(
				localStorage.token,
				activeTab,
				requestFilters(),
				currentController.signal
			);
			loadedTab = activeTab;
		} catch (message) {
			if (!currentController.signal.aborted) error = String(message);
		} finally {
			if (!currentController.signal.aborted) loading = false;
		}
	};

	const selectWorkflow = (id: string) => {
		filters.workflow_id = id;
		filters.configuration_id = '';
		activeTab = 'workflows';
		filtersChanged();
	};
	const selectConfiguration = (id: string) => {
		filters.configuration_id = id;
		filtersChanged();
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

	onMount(async () => {
		for (const key of Object.keys(filters))
			filters[key] = $pageStore.url.searchParams.get(key) ?? '';
		try {
			availableFilters = await getResearchFilters(localStorage.token);
		} catch (message) {
			error = String(message);
		}
		if (
			['essays', 'question-results', 'essay-stats', 'surveys'].includes($pageStore.params.tab ?? '')
		)
			syncUrl();
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

<div class="mx-auto min-w-0 w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8">
	<div class="mb-6">
		<h1 class="text-2xl font-semibold text-gray-900 dark:text-white">Researcher Dashboard</h1>
		<p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
			Workflow progress, participant results, and whole-run AI usage.
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
				bind:value={filters.workflow_id}
				on:change={() => {
					filters.configuration_id = '';
					filtersChanged();
				}}
				aria-label="Workflow filter"
				class="research-input"
			>
				<option value="">All workflows</option
				>{#each availableFilters.workflows ?? [] as workflow}<option value={workflow.id}
						>{workflow.name}</option
					>{/each}
			</select>
			<select
				bind:value={filters.configuration_id}
				on:change={filtersChanged}
				aria-label="Configuration filter"
				class="research-input"
				disabled={!filters.workflow_id}
			>
				<option value="">All configurations</option
				>{#each (availableFilters.workflows ?? []).find((w: { id: string }) => w.id === filters.workflow_id)?.configurations ?? [] as config}<option
						value={config.id}>{config.id.slice(0, 10)} · {config.runs} runs</option
					>{/each}
			</select>
			<select
				bind:value={filters.condition_key}
				on:change={filtersChanged}
				aria-label="Condition filter"
				class="research-input"
			>
				<option value="">All conditions</option
				>{#each availableFilters.conditions ?? [] as condition}<option value={condition.id}
						>{condition.name}</option
					>{/each}
			</select>
			<select
				bind:value={filters.data_kind}
				on:change={filtersChanged}
				aria-label="Data kind filter"
				class="research-input"
				><option value="">All data</option><option value="demo">Synthetic demos</option><option
					value="non_demo">Non-demo data</option
				></select
			>

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
		<Overview {data} onWorkflow={selectWorkflow} />
	{:else if activeTab === 'workflows'}
		<Workflows
			{data}
			filters={requestFilters()}
			onWorkflow={selectWorkflow}
			onConfiguration={selectConfiguration}
			onOpen={openDetail}
			onOpenQuestion={openQuestionDetail}
			{fmtDate}
		/>
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
		<Participants
			{data}
			{selected}
			{page}
			{limit}
			onPage={changePage}
			onOpen={openDetail}
			onToggle={toggleSelected}
			onSort={sortBy}
			{fmtDuration}
			{fmtDate}
		/>
	{:else if activeTab === 'usage'}
		<Usage {data} onOpen={openDetail} />
	{/if}
</div>

<SessionDetail
	bind:detailLoading
	bind:detail
	bind:anonymized
	bind:tabActivity
	bind:tabActivityLoading
	bind:tabEventType
	bind:tabBrowserId
	bind:tabWindowId
	bind:tabDateFrom
	bind:tabDateTo
	bind:tabActivityPage
	{closeDetail}
	{runFullSessionExport}
	{fmtDate}
	{openQuestionDetail}
	{fmtDuration}
	{loadTabActivity}
/>

<GradingDetail
	bind:questionDetail
	bind:questionDetailLoading
	bind:scoreDrafts
	bind:scoreNotes
	{closeQuestionDetail}
	{saveQuestionScore}
	{retryGrading}
/>

<style>
	:global(.research-input) {
		min-width: 0;
		max-width: 100%;
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
	:global(.research-json) {
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
