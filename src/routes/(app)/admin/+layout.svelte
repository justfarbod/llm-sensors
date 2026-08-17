<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Popover } from 'bits-ui';

	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { WEBUI_NAME, user } from '$lib/stores';
	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	const i18n: Writable<i18nType> = getContext('i18n');

	let loaded = false;
	let showAdvanced = false;

	const primaryLinks = [
		{ label: 'Research Dashboard', href: '/admin/analytics/overview', match: '/admin/analytics' },
		{ label: 'Users', href: '/admin/users', match: '/admin/users' },
		{ label: 'Experiment Tasks', href: '/admin/essays', match: '/admin/essays' }
	];

	const advancedLinks = [
		{ label: 'Functions', href: '/admin/functions' },
		{ label: 'Evaluations', href: '/admin/evaluations' },
		{ label: 'Settings', href: '/admin/settings' },
		{ label: 'System Analytics', href: '/admin/system-analytics' }
	];

	onMount(async () => {
		if ($user?.role !== 'admin') {
			await goto('/');
			return;
		}
		loaded = true;
	});
</script>

<svelte:head>
	<title>Admin Panel • {$WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	<div class="flex h-screen max-h-[100dvh] w-full flex-1 flex-col bg-white dark:bg-gray-950">
		<header
			class="z-20 flex min-h-14 items-center gap-3 border-b border-gray-100 bg-white/95 px-3 backdrop-blur-xl dark:border-gray-850 dark:bg-gray-950/95 sm:px-5"
		>
			<a
				href="/admin/analytics/overview"
				class="mr-1 shrink-0 rounded-lg px-2 py-1 text-sm font-semibold text-gray-900 hover:bg-gray-100 dark:text-white dark:hover:bg-gray-850"
			>
				AI-Assisted Writing Study
			</a>

			<nav
				aria-label="Admin navigation"
				class="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto"
			>
				{#each primaryLinks as link}
					<a
						href={link.href}
						class="min-w-fit rounded-lg px-2.5 py-1.5 text-sm font-medium transition {$page.url.pathname.startsWith(
							link.match
						)
							? 'bg-gray-100 text-gray-900 dark:bg-gray-850 dark:text-white'
							: 'text-gray-500 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-900 dark:hover:text-white'}"
					>
						{$i18n.t(link.label)}
					</a>
				{/each}

				<div>
					<Popover.Root bind:open={showAdvanced}>
						<Popover.Trigger
							id="admin-advanced-trigger"
							openOnHover={true}
							openDelay={80}
							closeDelay={220}
							class="flex min-w-fit items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium text-gray-500 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-900 dark:hover:text-white"
						>
							<EllipsisHorizontal className="size-4" />
							<span>Advanced</span>
						</Popover.Trigger>
						<Popover.Portal>
							<Popover.Content
								align="start"
								sideOffset={4}
								trapFocus={false}
								role="menu"
								aria-label="Advanced admin navigation"
								class="z-[9999] w-52 rounded-xl border border-gray-100 bg-white p-1 shadow-xl outline-none dark:border-gray-800 dark:bg-gray-900"
							>
								{#each advancedLinks as link}
									<a
										href={link.href}
										role="menuitem"
										on:click={() => (showAdvanced = false)}
										class="block rounded-lg px-3 py-2 text-sm outline-none {$page.url.pathname.startsWith(
											link.href
										)
											? 'bg-gray-100 font-medium text-gray-900 dark:bg-gray-800 dark:text-white'
											: 'text-gray-600 hover:bg-gray-100 hover:text-gray-900 focus:bg-gray-100 focus:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white dark:focus:bg-gray-800 dark:focus:text-white'}"
									>
										{$i18n.t(link.label)}
									</a>
								{/each}
							</Popover.Content>
						</Popover.Portal>
					</Popover.Root>
				</div>
			</nav>

			<UserMenu restricted={true} role="admin" showActiveUsers={false}>
				<button
					type="button"
					aria-label="Account menu"
					class="rounded-full p-0.5 hover:bg-gray-100 dark:hover:bg-gray-850"
				>
					<img
						src={`${WEBUI_API_BASE_URL}/users/${$user?.id}/profile/image`}
						class="size-8 rounded-full object-cover"
						alt=""
					/>
				</button>
			</UserMenu>
		</header>

		<main class="min-h-0 flex-1 overflow-y-auto">
			<slot />
		</main>
	</div>
{/if}
