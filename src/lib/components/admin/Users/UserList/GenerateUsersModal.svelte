<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, getContext } from 'svelte';

	import { bulkAddUsers } from '$lib/apis/auths';
	import { getGroups } from '$lib/apis/groups';
	import { downloadCSV } from '$lib/utils';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;

	let loading = false;
	let groups: Array<{ id: string; name: string }> = [];

	const defaultForm = () => ({
		prefix: '',
		count: 10,
		domain: '',
		role: 'user',
		group_id: '',
		start_index: 1,
		password_length: 12
	});

	let form = defaultForm();
	let result: { created: any[]; failed: any[]; group_id: string | null } | null = null;

	$: if (show) {
		result = null;
		form = defaultForm();
		getGroupList();
	}

	const getGroupList = async () => {
		const res = await getGroups(localStorage.token).catch(() => null);
		groups = Array.isArray(res) ? res : [];
	};

	const submitHandler = async () => {
		loading = true;

		const res = await bulkAddUsers(
			localStorage.token,
			form.prefix,
			form.count,
			form.domain,
			form.role,
			form.group_id || null,
			form.start_index,
			form.password_length
		).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			result = res;

			if (res.created.length > 0) {
				toast.success(
					$i18n.t('Successfully generated {{count}} users.', { count: res.created.length })
				);
				dispatch('save');
			}

			if (res.failed.length > 0) {
				toast.error($i18n.t('Failed to generate {{count}} users.', { count: res.failed.length }));
			}
		}

		loading = false;
	};

	const downloadHandler = () => {
		if (!result) return;

		downloadCSV(
			`${form.prefix || 'generated'}_users.csv`,
			['Name', 'Email', 'Password', 'Role'],
			result.created.map((u) => [u.name, u.email, u.password, u.role])
		);
	};
</script>

<Modal size="md" bind:show>
	<div>
		<div class=" flex justify-between dark:text-gray-300 px-5 pt-4 pb-2">
			<div class=" text-lg font-medium self-center">{$i18n.t('Generate Users')}</div>
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
			{#if !result}
				<form
					class="flex flex-col w-full"
					on:submit|preventDefault={() => {
						submitHandler();
					}}
				>
					<div class="flex flex-col w-full mb-3">
						<div class=" mb-1 text-xs text-gray-500">{$i18n.t('Username Prefix')}</div>
						<input
							class="w-full text-sm bg-transparent outline-hidden"
							type="text"
							bind:value={form.prefix}
							aria-label={$i18n.t('Username Prefix')}
							placeholder={$i18n.t('e.g. study')}
							autocomplete="off"
							required
						/>
					</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2.5 w-full" />

					<div class="flex gap-2 w-full mb-3">
						<div class="flex flex-col w-full">
							<div class=" mb-1 text-xs text-gray-500">{$i18n.t('Number of Users')}</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								min="1"
								max="200"
								bind:value={form.count}
								aria-label={$i18n.t('Number of Users')}
								required
							/>
						</div>

						<div class="flex flex-col w-full">
							<div class=" mb-1 text-xs text-gray-500">{$i18n.t('Start Number')}</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								min="1"
								bind:value={form.start_index}
								aria-label={$i18n.t('Start Number')}
							/>
						</div>
					</div>

					<div class="flex flex-col w-full mb-3">
						<div class=" mb-1 text-xs text-gray-500">{$i18n.t('Email Domain')}</div>
						<input
							class="w-full text-sm bg-transparent outline-hidden"
							type="text"
							bind:value={form.domain}
							aria-label={$i18n.t('Email Domain')}
							placeholder={$i18n.t('e.g. study.local')}
							autocomplete="off"
							required
						/>
						<div class="text-xs text-gray-500 mt-1">
							ⓘ {$i18n.t(
								'Each generated user gets an email of the form username@domain, since sign-in requires an email.'
							)}
						</div>
					</div>

					<div class="flex gap-2 w-full mb-3">
						<div class="flex flex-col w-full">
							<div class=" mb-1 text-xs text-gray-500">{$i18n.t('Role')}</div>
							<select
								class="w-full capitalize text-sm bg-transparent outline-hidden"
								bind:value={form.role}
								aria-label={$i18n.t('Role')}
								required
							>
								<option value="pending">{$i18n.t('pending')}</option>
								<option value="user">{$i18n.t('user')}</option>
								<option value="admin">{$i18n.t('admin')}</option>
							</select>
						</div>

						<div class="flex flex-col w-full">
							<div class=" mb-1 text-xs text-gray-500">{$i18n.t('Password Length')}</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="number"
								min="8"
								max="64"
								bind:value={form.password_length}
								aria-label={$i18n.t('Password Length')}
							/>
						</div>
					</div>

					<div class="flex flex-col w-full mb-1">
						<div class=" mb-1 text-xs text-gray-500">{$i18n.t('Group')}</div>
						<select
							class="w-full text-sm bg-transparent outline-hidden"
							bind:value={form.group_id}
							aria-label={$i18n.t('Group')}
						>
							<option value="">{$i18n.t('No Group')}</option>
							{#each groups as group}
								<option value={group.id}>{group.name}</option>
							{/each}
						</select>
					</div>

					<div class="flex justify-end pt-3 text-sm font-medium">
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-2 whitespace-nowrap {loading
								? ' cursor-not-allowed'
								: ''}"
							type="submit"
							disabled={loading}
						>
							{$i18n.t('Generate')}

							{#if loading}
								<span class="shrink-0">
									<Spinner />
								</span>
							{/if}
						</button>
					</div>
				</form>
			{:else}
				<div class="flex flex-col w-full">
					<div class="text-xs text-yellow-600 dark:text-yellow-500 mb-2">
						ⓘ {$i18n.t(
							'These passwords are shown only once and cannot be recovered later. Download or copy them now.'
						)}
					</div>

					{#if result.created.length > 0}
						<div
							class="max-h-72 overflow-y-auto border border-gray-100 dark:border-gray-850 rounded-xl mb-3"
						>
							<table class="w-full text-xs text-left">
								<thead class="text-gray-500 uppercase sticky top-0 bg-white dark:bg-gray-900">
									<tr>
										<th class="px-2.5 py-1.5">{$i18n.t('Name')}</th>
										<th class="px-2.5 py-1.5">{$i18n.t('Email')}</th>
										<th class="px-2.5 py-1.5">{$i18n.t('Password')}</th>
									</tr>
								</thead>
								<tbody>
									{#each result.created as u}
										<tr class="border-t border-gray-100 dark:border-gray-850">
											<td class="px-2.5 py-1">{u.name}</td>
											<td class="px-2.5 py-1">{u.email}</td>
											<td class="px-2.5 py-1 font-mono">{u.password}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}

					{#if result.failed.length > 0}
						<div class="text-xs text-red-500 mb-3">
							{$i18n.t('{{count}} users could not be created:', { count: result.failed.length })}
							<ul class="list-disc list-inside">
								{#each result.failed as f}
									<li>{f.name} — {f.reason}</li>
								{/each}
							</ul>
						</div>
					{/if}

					<div class="flex justify-end gap-2 pt-1 text-sm font-medium">
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-transparent hover:bg-gray-100 dark:hover:bg-gray-850 transition rounded-full whitespace-nowrap"
							type="button"
							on:click={() => {
								result = null;
							}}
						>
							{$i18n.t('Generate More')}
						</button>

						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full whitespace-nowrap"
							type="button"
							disabled={result.created.length === 0}
							on:click={downloadHandler}
						>
							{$i18n.t('Download CSV')}
						</button>
					</div>
				</div>
			{/if}
		</div>
	</div>
</Modal>

<style>
	input::-webkit-outer-spin-button,
	input::-webkit-inner-spin-button {
		-webkit-appearance: none;
		margin: 0;
	}

	input[type='number'] {
		-moz-appearance: textfield;
	}
</style>
