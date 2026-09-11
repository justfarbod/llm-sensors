<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { user } from '$lib/stores';
	onMount(() => {
		goto($user?.role === 'admin' ? '/admin/analytics/overview' : $user ? '/' : '/auth', {
			replaceState: true
		});
	});
</script>
