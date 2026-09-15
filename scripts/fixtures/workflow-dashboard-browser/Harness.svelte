<script>
import { setContext } from 'svelte';
import { writable } from 'svelte/store';
import ResearchDashboard from '../../../src/lib/components/admin/ResearchDashboard.svelte';
setContext('i18n', writable({t: (value) => value}));
</script>
<ResearchDashboard />
