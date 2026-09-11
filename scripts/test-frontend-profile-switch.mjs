import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

if (process.argv.includes('--probe')) {
	const { createServer } = await import('vite');
	const { profile, root, researchPaths } = await import('./frontend-profile.mjs');
	const { default: kit } = await import('../svelte.config.js');
	const server = await createServer({
		server: { middlewareMode: true, hmr: false },
		optimizeDeps: { noDiscovery: true, include: [] }
	});
	try {
		assert.ok(server.config.cacheDir.endsWith(`.vite-${profile}`));
		assert.equal(
			kit.kit.files?.routes ?? 'src/routes',
			profile === 'full' ? 'src/routes' : researchPaths.routes
		);
		assert.equal(
			kit.kit.files?.assets ?? 'static',
			profile === 'full' ? 'static' : researchPaths.assets
		);
		for (const source of [
			'src/lib/components/chat/Overview.svelte',
			'src/lib/components/chat/MessageInput/CallOverlay.svelte',
			'src/lib/components/notes/NoteEditor.svelte',
			'src/lib/workers/KokoroWorker.ts',
			'src/lib/workers/pyodide.worker.ts?worker'
		]) {
			const id = resolve(root, source);
			const result = await server.pluginContainer.resolveId(
				id,
				resolve(root, 'src/routes/+layout.svelte')
			);
			assert.ok(result, `Could not resolve ${source}`);
			if (profile === 'full') assert.equal(result.id, id);
			else assert.ok(result.id.includes('/disabled/'), `Research loaded ${result.id}`);
		}
		console.log(`${profile}: routes, assets, cache and optional imports verified`);
	} finally {
		await server.close();
	}
} else {
	for (const profile of ['full', 'research', 'full']) {
		const result = spawnSync(process.execPath, [fileURLToPath(import.meta.url), '--probe'], {
			env: { ...process.env, FRONTEND_PROFILE: profile },
			stdio: 'inherit',
			timeout: 60000
		});
		assert.equal(result.status, 0, `${profile} configuration probe failed`);
	}
	console.log(
		'Full → research → full configuration restoration passed (no production compilation).'
	);
}
