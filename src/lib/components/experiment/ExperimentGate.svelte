<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import * as FocusTrap from 'focus-trap';

	import {
		completeExperiment,
		getCurrentExperiment,
		startExperiment,
		submitExperimentConsent,
		submitExperimentPostSurvey,
		submitExperimentPreSurvey
	} from '$lib/apis/experiments';
	import { userSignOut } from '$lib/apis/auths';
	import { experimentCurrent, experimentRefresh, showEssaySidebar, user } from '$lib/stores';
	import ExperimentSurveyModal from './ExperimentSurveyModal.svelte';
	import {
		createExperimentStateLoader,
		experimentNeedsGate,
		sameExperimentState
	} from '$lib/utils/experiments';
	const i18n: Writable<i18nType> = getContext('i18n');

	let container: HTMLDivElement;
	let focusTrap: FocusTrap.FocusTrap | null = null;
	let loading = false;
	let agreed = false;
	let channel: BroadcastChannel | null = null;
	let refreshValue = 0;

	let pre = {
		school_class: '',
		ai_familiarity: '',
		ai_schoolwork_frequency: '',
		essay_writing_confidence: '',
		task_confidence: '',
		age_range: ''
	};
	let post: Record<string, string> = {
		ai_helpfulness: '',
		essay_satisfaction: '',
		task_satisfaction: '',
		ai_improvement: '',
		chat_ease: '',
		comments: ''
	};
	const prePayload = () =>
		$experimentCurrent?.survey_variant === 'TASK_NEUTRAL'
			? {
					school_class: pre.school_class,
					ai_familiarity: Number(pre.ai_familiarity),
					ai_schoolwork_frequency: pre.ai_schoolwork_frequency,
					task_confidence: Number(pre.task_confidence),
					age_range: pre.age_range
				}
			: {
					school_class: pre.school_class,
					ai_familiarity: Number(pre.ai_familiarity),
					ai_schoolwork_frequency: pre.ai_schoolwork_frequency,
					essay_writing_confidence: Number(pre.essay_writing_confidence),
					age_range: pre.age_range
				};
	const postPayload = () =>
		$experimentCurrent?.survey_variant === 'TASK_NEUTRAL'
			? {
					ai_helpfulness: Number(post.ai_helpfulness),
					task_satisfaction: Number(post.task_satisfaction),
					ai_improvement: post.ai_improvement,
					chat_ease: Number(post.chat_ease),
					comments: post.comments
				}
			: {
					ai_helpfulness: Number(post.ai_helpfulness),
					essay_satisfaction: Number(post.essay_satisfaction),
					ai_improvement: post.ai_improvement,
					chat_ease: Number(post.chat_ease),
					comments: post.comments
				};
	const loadExperimentState = createExperimentStateLoader();

	const setExperimentState = (next: Awaited<ReturnType<typeof getCurrentExperiment>>) => {
		const enteredWriting =
			next.state === 'IN_PROGRESS' && $experimentCurrent?.state !== 'IN_PROGRESS';
		if (!sameExperimentState($experimentCurrent, next)) experimentCurrent.set(next);
		window.dispatchEvent(
			new CustomEvent('open-webui-experiment-state', { detail: { state: next.state } })
		);
		if (enteredWriting) showEssaySidebar.set(true);
	};

	const refresh = async (force = false) => {
		if (!localStorage.token) return;
		try {
			const next = await loadExperimentState(
				$experimentCurrent,
				() => getCurrentExperiment(localStorage.token),
				force
			);
			if (next) setExperimentState(next);
		} catch (error) {
			console.error('Failed to load Experiment Mode state', error);
		}
	};

	const run = async (action: () => Promise<any>) => {
		if (loading) return;
		loading = true;
		try {
			setExperimentState(await action());
			channel?.postMessage('refresh');
		} catch (error) {
			toast.error(`${error}`);
			await refresh();
		} finally {
			loading = false;
		}
	};

	const finish = async () => {
		await run(async () => {
			const result = await completeExperiment(localStorage.token);
			const signout = await userSignOut();
			user.set(undefined);
			localStorage.removeItem('token');
			location.href = signout?.redirect_url ?? '/auth';
			return result;
		});
	};

	const signOut = async () => {
		window.dispatchEvent(
			new CustomEvent('open-webui-experiment-state', { detail: { state: 'SIGNED_OUT' } })
		);
		const result = await userSignOut();
		user.set(undefined);
		localStorage.removeItem('token');
		location.href = result?.redirect_url ?? '/auth';
	};

	const syncFocusTrap = (needsGate: boolean, element: HTMLDivElement | undefined) => {
		if (!needsGate || !element) {
			focusTrap?.deactivate();
			focusTrap = null;
			return;
		}

		tick().then(() => {
			if (!experimentNeedsGate($experimentCurrent) || element !== container) return;
			focusTrap?.deactivate();
			focusTrap = FocusTrap.createFocusTrap(element, {
				escapeDeactivates: false,
				allowOutsideClick: false,
				fallbackFocus: element
			});
			focusTrap.activate();
		});
	};

	// Keep focus-trap mutation outside this statement's dependencies to avoid self-invalidating loops.
	$: syncFocusTrap(experimentNeedsGate($experimentCurrent), container);

	$: if ($experimentRefresh !== refreshValue) {
		refreshValue = $experimentRefresh;
		refresh();
	}

	onMount(() => {
		refresh();
		channel = new BroadcastChannel('experiment-mode');
		channel.onmessage = () => refresh();
		const focus = () => refresh();
		window.addEventListener('focus', focus);
		return () => window.removeEventListener('focus', focus);
	});

	onDestroy(() => {
		focusTrap?.deactivate();
		channel?.close();
	});
</script>

<ExperimentSurveyModal />

{#if $experimentCurrent}
	{#if experimentNeedsGate($experimentCurrent)}
		<div
			bind:this={container}
			class="fixed inset-0 z-999999 flex min-h-[100dvh] items-center justify-center overflow-y-auto bg-white p-3 text-gray-900 dark:bg-gray-900 dark:text-gray-100 sm:p-6"
			role="dialog"
			aria-modal="true"
			aria-label="Experiment Mode"
		>
			<div
				class="my-auto w-full max-w-2xl rounded-3xl border border-gray-100 bg-white p-5 shadow-xl dark:border-gray-800 dark:bg-gray-850 sm:p-8"
			>
				{#if $experimentCurrent.state === 'CONSENT_REQUIRED'}
					<h1 class="text-xl font-semibold">Research Participation Agreement</h1>
					<div
						class="mt-4 max-h-[55dvh] overflow-y-auto whitespace-pre-wrap text-sm leading-6 text-gray-600 dark:text-gray-300"
					>
						{$experimentCurrent.agreement_text}
					</div>
					<label class="mt-5 flex items-start gap-3 text-sm">
						<input class="mt-1" type="checkbox" bind:checked={agreed} />
						<span>I have read and agree to participate.</span>
					</label>
					<button
						class="mt-6 w-full rounded-xl bg-black px-4 py-2.5 font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
						disabled={!agreed || loading}
						on:click={() => run(() => submitExperimentConsent(localStorage.token))}>Continue</button
					>
				{:else if $experimentCurrent.state === 'PRE_SURVEY_REQUIRED'}
					<h1 class="text-xl font-semibold">Pre-Survey</h1>
					<form
						class="mt-5 space-y-4"
						on:submit|preventDefault={() =>
							run(() => submitExperimentPreSurvey(localStorage.token, prePayload()))}
					>
						<label class="block text-sm"
							>What school class or grade are you currently in?<input
								class="mt-1 w-full rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-800"
								bind:value={pre.school_class}
								required
								maxlength="200"
							/></label
						>
						<label class="block text-sm"
							>How familiar are you with AI tools such as ChatGPT?<select
								class="mt-1 w-full rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-800"
								bind:value={pre.ai_familiarity}
								required
								><option value="" disabled>Select one</option
								>{#each [1, 2, 3, 4, 5] as value}<option {value}
										>{value} - {value === 1
											? 'Not familiar at all'
											: value === 5
												? 'Extremely familiar'
												: ''}</option
									>{/each}</select
							></label
						>
						<label class="block text-sm"
							>How often do you use AI tools for schoolwork?<select
								class="mt-1 w-full rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-800"
								bind:value={pre.ai_schoolwork_frequency}
								required
								><option value="" disabled>Select one</option
								>{#each ['Never', 'Rarely', 'Sometimes', 'Often', 'Very often'] as value}<option
										{value}>{value}</option
									>{/each}</select
							></label
						>
						{#if $experimentCurrent.survey_variant === 'TASK_NEUTRAL'}<label class="block text-sm"
								>{$i18n.t('How confident are you that you can complete the assigned tasks?')}<select
									class="mt-1 w-full rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-800"
									bind:value={pre.task_confidence}
									required
									><option value="" disabled>{$i18n.t('Select one')}</option
									>{#each [1, 2, 3, 4, 5] as value}<option {value}
											>{value} - {value === 1
												? $i18n.t('Not confident at all')
												: value === 5
													? $i18n.t('Very confident')
													: ''}</option
										>{/each}</select
								></label
							>{:else}<label class="block text-sm"
								>How confident are you in your essay-writing skills?<select
									class="mt-1 w-full rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-800"
									bind:value={pre.essay_writing_confidence}
									required
									><option value="" disabled>Select one</option
									>{#each [1, 2, 3, 4, 5] as value}<option {value}
											>{value} - {value === 1
												? 'Not confident at all'
												: value === 5
													? 'Very confident'
													: ''}</option
										>{/each}</select
								></label
							>{/if}
						<label class="block text-sm"
							>Which age range applies to you?<select
								class="mt-1 w-full rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-800"
								bind:value={pre.age_range}
								required
								><option value="" disabled>Select one</option
								>{#each ['Under 13', '13–14', '15–16', '17–18', 'Over 18', 'Prefer not to say'] as value}<option
										{value}>{value}</option
									>{/each}</select
							></label
						>
						<button
							class="w-full rounded-xl bg-black px-4 py-2.5 font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
							disabled={loading}>Submit Pre-Survey</button
						>
					</form>
				{:else if $experimentCurrent.state === 'TOPIC_REQUIRED' || $experimentCurrent.state === 'TASK_REQUIRED'}
					{#if $experimentCurrent.state === 'TASK_REQUIRED'}
						<h1 class="text-xl font-semibold">{$i18n.t('Your Experiment Tasks')}</h1>
						<p class="mt-2 text-sm text-gray-500">
							{$i18n.t('Complete the assigned tasks using the configured navigation mode.')}
						</p>
						<div class="mt-4 space-y-2">
							{#each $experimentCurrent.tasks ?? [] as task}<div
									class="rounded-xl bg-gray-100 px-3 py-2 text-sm dark:bg-gray-800"
								>
									{task.position + 1}. {$i18n.t(
										task.task_type === 'ESSAY'
											? 'Essay Task'
											: task.task_type === 'QUESTION'
												? 'Question Task'
												: 'Survey Task'
									)} — {task.title}
								</div>{/each}
						</div>
						<button
							class="mt-6 w-full rounded-xl bg-black px-4 py-2.5 font-medium text-white dark:bg-white dark:text-black"
							disabled={loading}
							on:click={() => run(() => startExperiment(localStorage.token))}
							>{$i18n.t('Start Tasks')}</button
						>
					{:else}
						<h1 class="text-xl font-semibold">Your Essay Topic</h1>
						<h2 class="mt-5 font-medium">{$experimentCurrent.topic?.title}</h2>
						<p class="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-600 dark:text-gray-300">
							{$experimentCurrent.topic?.question}
						</p>
						<button
							class="mt-6 w-full rounded-xl bg-black px-4 py-2.5 font-medium text-white dark:bg-white dark:text-black"
							disabled={loading}
							on:click={() => run(() => startExperiment(localStorage.token))}>Start Writing</button
						>
					{/if}
				{:else if $experimentCurrent.state === 'POST_SURVEY_REQUIRED'}
					<h1 class="text-xl font-semibold">Post-Survey</h1>
					<form
						class="mt-5 space-y-4"
						on:submit|preventDefault={() =>
							run(() => submitExperimentPostSurvey(localStorage.token, postPayload()))}
					>
						{#each $experimentCurrent.survey_variant === 'TASK_NEUTRAL' ? [['ai_helpfulness', $i18n.t('How helpful was the AI assistant while completing the tasks?'), $i18n.t('Not helpful at all'), $i18n.t('Extremely helpful')], ['task_satisfaction', $i18n.t('How satisfied are you with the work you submitted?'), $i18n.t('Not satisfied at all'), $i18n.t('Very satisfied')], ['chat_ease', $i18n.t('How easy was it to use the AI chat?'), $i18n.t('Very difficult'), $i18n.t('Very easy')]] : [['ai_helpfulness', 'How helpful was the AI assistant while writing your essay?', 'Not helpful at all', 'Extremely helpful'], ['essay_satisfaction', 'How satisfied are you with the essay you submitted?', 'Not satisfied at all', 'Very satisfied'], ['chat_ease', 'How easy was it to use the AI chat while writing?', 'Very difficult', 'Very easy']] as question}
							<label class="block text-sm"
								>{question[1]}<select
									class="mt-1 w-full rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-800"
									bind:value={post[question[0]]}
									required
									><option value="" disabled>Select one</option
									>{#each [1, 2, 3, 4, 5] as value}<option {value}
											>{value} - {value === 1
												? question[2]
												: value === 5
													? question[3]
													: ''}</option
										>{/each}</select
								></label
							>
						{/each}
						<label class="block text-sm"
							>{$experimentCurrent.survey_variant === 'TASK_NEUTRAL'
								? $i18n.t('How much do you think the AI assistant improved your submitted work?')
								: 'How much do you think the AI assistant improved your essay?'}<select
								class="mt-1 w-full rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-800"
								bind:value={post.ai_improvement}
								required
								><option value="" disabled>Select one</option
								>{#each ['Not at all', 'A little', 'A moderate amount', 'A lot', 'A great deal'] as value}<option
										{value}>{value}</option
									>{/each}</select
							></label
						>
						<label class="block text-sm"
							>Do you have any comments about your experience?<textarea
								class="mt-1 w-full rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-800"
								rows="3"
								maxlength="4000"
								bind:value={post.comments}
							></textarea></label
						>
						<button
							class="w-full rounded-xl bg-black px-4 py-2.5 font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
							disabled={loading}>Submit Post-Survey</button
						>
					</form>
				{:else if $experimentCurrent.state === 'THANK_YOU_REQUIRED'}
					<h1 class="text-xl font-semibold">Thank you for participating.</h1>
					<p class="mt-4 text-sm leading-6 text-gray-600 dark:text-gray-300">
						Your responses have been submitted successfully. Please click the button below to finish
						your session.
					</p>
					<button
						class="mt-6 w-full rounded-xl bg-black px-4 py-2.5 font-medium text-white dark:bg-white dark:text-black"
						disabled={loading}
						on:click={finish}>Finish Session</button
					>
				{:else}
					<h1 class="text-xl font-semibold">Experiment Session Unavailable</h1>
					<p class="mt-4 text-sm leading-6 text-gray-600 dark:text-gray-300">
						{$experimentCurrent.error ??
							($experimentCurrent.state === 'COMPLETED'
								? 'This experiment session has already been completed.'
								: 'Please contact an administrator.')}
					</p>
					<button
						class="mt-6 w-full rounded-xl bg-gray-100 px-4 py-2.5 font-medium text-gray-900 dark:bg-gray-800 dark:text-white"
						on:click={signOut}>Sign Out</button
					>
				{/if}
			</div>
		</div>
	{/if}
{/if}
