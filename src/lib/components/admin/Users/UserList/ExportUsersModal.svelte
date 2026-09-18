<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext } from 'svelte';

	import { exportUsers } from '$lib/apis/users';
	import { getGroups } from '$lib/apis/groups';
	import { downloadCSV } from '$lib/utils';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext('i18n');

	export let show = false;

	const ALL_USERS = '';
	const NO_GROUP = '__no_group__';

	let loading = false;
	let groups: Array<{ id: string; name: string }> = [];
	let selectedGroupId = ALL_USERS;

	$: if (show) {
		selectedGroupId = ALL_USERS;
		getGroupList();
	}

	const getGroupList = async () => {
		const res = await getGroups(localStorage.token).catch(() => null);
		groups = Array.isArray(res) ? res : [];
	};

	const exportHandler = async () => {
		loading = true;

		const groupId = selectedGroupId !== ALL_USERS && selectedGroupId !== NO_GROUP ? selectedGroupId : null;
		const noGroup = selectedGroupId === NO_GROUP;

		const res = await exportUsers(localStorage.token, groupId, noGroup).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			if (res.length === 0) {
				toast.info($i18n.t('No users to export.'));
			} else {
				const groupName = groups.find((g) => g.id === groupId)?.name;
				const suffix = noGroup ? 'no_group' : (groupName ?? (groupId ? groupId : 'all'));

				downloadCSV(
					`users_${suffix}.csv`,
					['Name', 'Email', 'Role', 'Created At'],
					res.map((u) => [
						u.name,
						u.email,
						u.role,
						new Date(u.created_at * 1000).toISOString()
					])
				);
				toast.success($i18n.t('Successfully exported {{count}} users.', { count: res.length }));
			}
		}

		loading = false;
	};
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between dark:text-gray-300 px-5 pt-4 pb-2">
			<div class=" text-lg font-medium self-center">{$i18n.t('Export Users')}</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="flex flex-col w-full px-4 pb-4 dark:text-gray-200">
			<div class="flex flex-col w-full mb-1">
				<div class=" mb-1 text-xs text-gray-500">{$i18n.t('Group')}</div>
				<select
					class="w-full text-sm bg-transparent outline-hidden"
					bind:value={selectedGroupId}
					aria-label={$i18n.t('Group')}
				>
					<option value={ALL_USERS}>{$i18n.t('All Users')}</option>
					<option value={NO_GROUP}>{$i18n.t('No Group')}</option>
					{#each groups as group}
						<option value={group.id}>{group.name}</option>
					{/each}
				</select>
			</div>

			<div class="text-xs text-gray-500 mt-1 mb-3">
				ⓘ {$i18n.t(
					'Passwords cannot be exported for existing users, since only their hashes are stored.'
				)}
			</div>

			<div class="flex justify-end pt-1 text-sm font-medium">
				<button
					class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-2 whitespace-nowrap {loading
						? ' cursor-not-allowed'
						: ''}"
					type="button"
					disabled={loading}
					on:click={exportHandler}
				>
					{$i18n.t('Export CSV')}

					{#if loading}
						<span class="shrink-0">
							<Spinner />
						</span>
					{/if}
				</button>
			</div>
		</div>
	</div>
</Modal>
