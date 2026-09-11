import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import {
	appendFileSync,
	existsSync,
	mkdirSync,
	readFileSync,
	rmSync,
	writeFileSync
} from 'node:fs';
import { resolve } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import { root, researchPaths } from './frontend-profile.mjs';

// No production routes are changed: this test owns a uniquely named temporary
// canonical route and removes it (and the dev server) even if a check fails.
const route = resolve(root, 'src/routes/__profile_watch_test');
const generated = resolve(researchPaths.routes, '__profile_watch_test');
assert.equal(existsSync(route), false, 'Refusing to overwrite an existing route');
const port = process.env.PROFILE_TEST_PORT || '15173';
mkdirSync(resolve(root, '.generated/validation'), { recursive: true });
const server = spawn('npm', ['run', 'dev:5050', '--', '--port', port, '--strictPort'], {
	cwd: root,
	stdio: ['ignore', 'pipe', 'pipe'],
	detached: true
});
let output = '';
server.stdout.on('data', (chunk) => {
	output += chunk;
	appendFileSync(resolve(root, '.generated/validation/dev-server.log'), chunk);
});
server.stderr.on('data', (chunk) => {
	output += chunk;
	appendFileSync(resolve(root, '.generated/validation/dev-server.log'), chunk);
});
async function until(check) {
	const end = Date.now() + 30000;
	while (Date.now() < end) {
		if (await check()) return;
		if (server.exitCode !== null) throw new Error(output);
		await pause(100);
	}
	throw new Error(`Timed out. Dev output: ${output.slice(-3000)}`);
}
try {
	await until(() => output.includes(`:${port}/`));
	assert.equal(/Setting up pyodide/.test(output), false);
	mkdirSync(route);
	writeFileSync(resolve(route, '+page.svelte'), '<p>Profile watch first</p>');
	await until(() => existsSync(resolve(generated, '+page.svelte')));
	const url = `http://127.0.0.1:${port}/.generated/research/routes/__profile_watch_test/+page.svelte`;
	let response = await fetch(url, { signal: AbortSignal.timeout(60000) });
	assert.equal(response.status, 200);
	assert.match(await response.text(), /Profile watch first/);
	writeFileSync(resolve(route, '+page.svelte'), '<p>Profile watch updated</p>');
	await until(() => readFileSync(resolve(generated, '+page.svelte'), 'utf8').includes('updated'));
	await until(async () => {
		try {
			response = await fetch(url, { signal: AbortSignal.timeout(60000) });
			return (await response.text()).includes('Profile watch updated');
		} catch (error) {
			if (error.cause?.code === 'ECONNRESET') return false;
			throw error;
		}
	});
	rmSync(resolve(route, '+page.svelte'));
	await until(() => !existsSync(resolve(generated, '+page.svelte')));
	console.log('Alternate port forwarding and live canonical route addition/edit/deletion passed.');
} finally {
	rmSync(route, { recursive: true, force: true });
	try {
		process.kill(-server.pid, 'SIGTERM');
	} catch (error) {
		if (error.code !== 'ESRCH') throw error;
	}
	await Promise.race([new Promise((done) => server.on('exit', done)), pause(3000)]);
	if (server.exitCode === null) {
		try {
			process.kill(-server.pid, 'SIGKILL');
		} catch {}
	}
	rmSync(generated, { recursive: true, force: true });
}
