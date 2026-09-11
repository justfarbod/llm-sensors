import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { loadEnv } from 'vite';

const root = resolve(import.meta.dirname);
const output = resolve(process.env.EXPERIMENT_TELEMETRY_EXTENSION_OUTPUT ?? resolve(root, 'dist'));
if (output === root || output === resolve('/'))
	throw new Error('Refusing to overwrite an unsafe output path.');
// Share the frontend's local configuration instead of requiring a separate,
// easily mismatched origin on every extension build. Explicit environment
// variables still take precedence for production and isolated test builds.
const settings = loadEnv('development', resolve(root, '../..'), 'EXPERIMENT_TELEMETRY_EXTENSION_');
const rawOrigin = settings.EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN ?? '';
if (!rawOrigin)
	throw new Error('Set EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN in .env or the build environment.');
const parsedOrigin = new URL(rawOrigin);
const origin = parsedOrigin.origin;
const loopback = ['localhost', '127.0.0.1', '::1'].includes(parsedOrigin.hostname);
if (
	(!origin.startsWith('https://') && !(parsedOrigin.protocol === 'http:' && loopback)) ||
	origin !== rawOrigin.replace(/\/$/, '')
) {
	throw new Error(
		'EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN must be one exact HTTPS origin, or an HTTP loopback origin for development.'
	);
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const name of [
	'background.js',
	'content.js',
	'connection.js',
	'tab-normalization.js',
	'README.md'
])
	await cp(resolve(root, name), resolve(output, name));
for (const name of ['manifest.json', 'deployment-config.js']) {
	const source = await readFile(resolve(root, name), 'utf8');
	await writeFile(resolve(output, name), source.replaceAll('__OPEN_WEBUI_ORIGIN__', origin));
}
console.log(`Built fixed-origin extension in ${output} for ${origin}`);
console.log('Reload the unpacked extension in chrome://extensions to activate this build.');
