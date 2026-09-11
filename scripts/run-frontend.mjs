import { spawn } from 'node:child_process';
import { rmSync } from 'node:fs';
import { resolve } from 'node:path';
import { resolveProfile } from '../src/lib/features/definition.js';

const [requestedProfile, command, ...args] = process.argv.slice(2);
const profile = resolveProfile(requestedProfile);
if (!['dev', 'build'].includes(command)) throw new Error(`Unknown frontend command: ${command}`);
process.env.FRONTEND_PROFILE = profile;

async function run(script, scriptArgs = []) {
	const child = spawn(process.execPath, [script, ...scriptArgs], {
		stdio: 'inherit',
		env: process.env
	});
	const forward = (signal) => child.kill(signal);
	const interrupt = () => forward('SIGINT');
	const terminate = () => forward('SIGTERM');
	process.on('SIGINT', interrupt);
	process.on('SIGTERM', terminate);
	try {
		const code = await new Promise((resolve, reject) => {
			child.on('error', reject);
			child.on('exit', (code, signal) => resolve(code ?? (signal ? 1 : 0)));
		});
		if (code !== 0) process.exit(code);
	} finally {
		process.off('SIGINT', interrupt);
		process.off('SIGTERM', terminate);
	}
}

if (profile === 'full') await run('scripts/prepare-pyodide.js');
if (command === 'build') rmSync(resolve('build'), { recursive: true, force: true });
await run('node_modules/vite/bin/vite.js', [command, ...args]);
