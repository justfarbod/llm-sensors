<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	import { chats, chatId, mobile, showSidebar, user } from '$lib/stores';
	import { getChatList } from '$lib/apis/chats';
	import ChatItem from '$lib/components/layout/Sidebar/ChatItem.svelte';
	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import PencilSquare from '$lib/components/icons/PencilSquare.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	const loadChats = async () => chats.set(await getChatList(localStorage.token, 1).catch(() => []));
	const newChat = async () => {
		chatId.set('');
		await goto('/');
		if ($mobile) showSidebar.set(false);
	};
	let chatItems: any[] = [];
	$: chatItems = ($chats ?? []) as any[];

	onMount(loadChats);
</script>

{#if $showSidebar}
	{#if $mobile}
		<button
			class="fixed inset-0 z-40 bg-black/60"
			aria-label="Close sidebar"
			on:click={() => showSidebar.set(false)}
		></button>
	{/if}
	<aside
		class="fixed left-0 top-0 z-50 flex h-[100dvh] w-[var(--sidebar-width)] flex-col border-r border-gray-100 bg-gray-50/95 p-2 text-gray-900 dark:border-gray-850 dark:bg-gray-950/95 dark:text-gray-100"
	>
		<div class="flex items-center gap-2 pb-2">
			<button class="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900" on:click={newChat}>
				<PencilSquare className="size-5" />
			</button>
			<button class="flex-1 text-left text-sm font-medium" on:click={newChat}>New Chat</button>
			<button
				class="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900"
				aria-label="Close sidebar"
				on:click={() => showSidebar.set(false)}
			>
				<SidebarIcon className="size-5" />
			</button>
		</div>
		<div class="flex-1 space-y-1 overflow-y-auto">
			{#each chatItems as chat (chat.id)}
				<ChatItem
					id={chat.id}
					title={chat.title}
					createdAt={chat.created_at}
					updatedAt={chat.updated_at}
					lastReadAt={chat.last_read_at}
					restricted={true}
					on:change={loadChats}
				/>
			{/each}
		</div>
		<UserMenu restricted={true} role={$user?.role}>
			<div
				class="flex w-full items-center gap-3 rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900"
			>
				<img
					src={`${WEBUI_API_BASE_URL}/users/${$user?.id}/profile/image`}
					class="size-7 rounded-full object-cover"
					alt="Open user menu"
				/>
				<span class="truncate text-sm font-medium">{$user?.name}</span>
			</div>
		</UserMenu>
	</aside>
{:else if !$mobile}
	<aside
		class="flex h-full w-[49px] flex-col justify-between border-r border-gray-100 p-1.5 dark:border-gray-850"
	>
		<div class="space-y-1">
			<button
				class="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900"
				aria-label="Open sidebar"
				on:click={() => showSidebar.set(true)}
			>
				<SidebarIcon className="size-5" />
			</button>
			<button
				class="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900"
				aria-label="New chat"
				on:click={newChat}
			>
				<PencilSquare className="size-5" />
			</button>
		</div>
		<UserMenu restricted={true} role={$user?.role}>
			<img
				src={`${WEBUI_API_BASE_URL}/users/${$user?.id}/profile/image`}
				class="size-7 rounded-full object-cover"
				alt="Open user menu"
			/>
		</UserMenu>
	</aside>
{/if}
