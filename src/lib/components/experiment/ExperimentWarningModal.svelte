<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import type { i18n as i18nType } from 'i18next';
	import type { Writable } from 'svelte/store';

	import {
		acknowledgeExperimentWarning,
		getExperimentRuntime,
		reportExperimentActivity,
		reportWarningDisplay,
		type ExperimentWarning
	} from '$lib/apis/experiments';
	import { experimentCurrent, experimentResponseActive } from '$lib/stores';
	import { boundedActiveElapsed } from '$lib/utils/experimentPerturbations';

	const i18n: Writable<i18nType> = getContext('i18n');
	let warning: ExperimentWarning | null = null;
	let displayedToken: string | null = null;
	let button: HTMLButtonElement;
	let timer: ReturnType<typeof setInterval> | null = null;
	let activeStartedAt: number | null = null;
	let submitting = false;

	const applyRuntime = async (runtime: { warning: ExperimentWarning | null } | null) => {
		if (runtime?.warning) warning = runtime.warning;
		if (warning && !$experimentResponseActive && displayedToken !== warning.token) {
			displayedToken = warning.token;
			await reportWarningDisplay(localStorage.token, warning.token).catch(() => null);
			await tick();
			button?.focus();
		}
	};

	const refresh = async () => {
		if ($experimentCurrent?.state !== 'IN_PROGRESS') return;
		await applyRuntime(await getExperimentRuntime(localStorage.token).catch(() => null));
	};

	const heartbeat = async () => {
		if ($experimentCurrent?.state !== 'IN_PROGRESS') {
			activeStartedAt = null;
			warning = null;
			return;
		}
		const active = document.visibilityState === 'visible' && document.hasFocus();
		const now = performance.now();
		if (!active) {
			const elapsed = boundedActiveElapsed(activeStartedAt, now, activeStartedAt !== null);
			activeStartedAt = null;
			if (elapsed > 0) {
				await applyRuntime(
					await reportExperimentActivity(localStorage.token, Math.round(elapsed)).catch(() => null)
				);
			}
			return;
		}
		const elapsed = boundedActiveElapsed(activeStartedAt, now, active);
		activeStartedAt = now;
		if (elapsed > 0) {
			await applyRuntime(
				await reportExperimentActivity(localStorage.token, Math.round(elapsed)).catch(() => null)
			);
		} else {
			await refresh();
		}
	};

	const acknowledge = async () => {
		if (!warning || submitting) return;
		submitting = true;
		try {
			await acknowledgeExperimentWarning(localStorage.token, warning.token);
			warning = null;
			displayedToken = null;
		} finally {
			submitting = false;
		}
	};

	const handleKeydown = (event: KeyboardEvent) => {
		if (!warning || $experimentResponseActive) return;
		if (event.key === 'Escape') {
			event.preventDefault();
			if (!warning.must_acknowledge) void acknowledge();
		}
		if (event.key === 'Tab') {
			event.preventDefault();
			button?.focus();
		}
	};

	$: if (warning && !$experimentResponseActive && displayedToken !== warning.token) {
		void applyRuntime({ warning });
	}

	onMount(() => {
		void heartbeat();
		timer = setInterval(heartbeat, 10000);
		window.addEventListener('focus', heartbeat);
		window.addEventListener('blur', heartbeat);
		document.addEventListener('visibilitychange', heartbeat);
		window.addEventListener('keydown', handleKeydown, true);
	});

	onDestroy(() => {
		if (timer) clearInterval(timer);
		window.removeEventListener('focus', heartbeat);
		window.removeEventListener('blur', heartbeat);
		document.removeEventListener('visibilitychange', heartbeat);
		window.removeEventListener('keydown', handleKeydown, true);
	});
</script>

{#if warning && !$experimentResponseActive && displayedToken === warning.token}
	<div
		class="fixed inset-0 z-[10000] flex items-center justify-center overflow-y-auto bg-black/40 p-4"
		role="presentation"
		on:mousedown={(event) => {
			if (event.currentTarget === event.target && !warning?.must_acknowledge) void acknowledge();
		}}
	>
		<div
			class="w-full max-w-lg overflow-hidden rounded-2xl bg-white p-6 shadow-2xl dark:bg-gray-900"
			role="dialog"
			aria-modal="true"
			aria-live="assertive"
			aria-labelledby="experiment-warning-title"
			aria-describedby="experiment-warning-message"
		>
			<h2 id="experiment-warning-title" class="text-lg font-semibold">{warning.title}</h2>
			<p
				id="experiment-warning-message"
				class="mt-3 whitespace-pre-wrap text-sm leading-6 text-gray-600 dark:text-gray-300"
			>
				{warning.message}
			</p>
			<div class="mt-6 flex justify-end">
				<button
					bind:this={button}
					class="rounded-full bg-black px-5 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
					disabled={submitting}
					on:click={acknowledge}
				>
					{warning.confirmation_text || $i18n.t('Continue')}
				</button>
			</div>
		</div>
	</div>
{/if}
