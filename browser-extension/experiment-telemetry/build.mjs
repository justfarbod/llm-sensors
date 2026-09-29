import { cp, mkdir, rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { loadEnv } from 'vite';
import { setOrigin } from './set-origin.mjs';

const root = resolve(import.meta.dirname);
const output = resolve(process.env.EXPERIMENT_TELEMETRY_EXTENSION_OUTPUT ?? resolve(root, 'dist'));
if (output === root || output === resolve('/'))
	throw new Error('Refusing to overwrite an unsafe output path.');
// The origin is optional: without it the build keeps the manifest's default
// origin, and any built folder can be retargeted later with set-origin.mjs.
// When set (in the frontend's local .env or the environment, which takes
// precedence), it is applied to the built manifest.
const settings = loadEnv('development', resolve(root, '../..'), 'EXPERIMENT_TELEMETRY_EXTENSION_');
const rawOrigin = settings.EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN ?? '';

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const name of [
	'manifest.json',
	'deployment-config.js',
	'background.js',
	'content.js',
	'connection.js',
	'tab-normalization.js',
	'README.md'
])
	await cp(resolve(root, name), resolve(output, name));
if (rawOrigin) {
	const origin = await setOrigin(output, rawOrigin);
	console.log(`Built extension in ${output} for ${origin}`);
} else {
	console.log(`Built extension in ${output} with the manifest's default origin.`);
	console.log(`Retarget it with: node ${resolve(root, 'set-origin.mjs')} ${output} https://domain`);
}
console.log('Reload the unpacked extension in chrome://extensions to activate this build.');
