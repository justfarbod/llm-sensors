import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const LOOPBACK = ['localhost', '127.0.0.1', '[::1]'];

export const validateOrigin = (rawOrigin) => {
	let parsed;
	try {
		parsed = new URL(rawOrigin);
	} catch {
		parsed = null;
	}
	const origin = parsed?.origin;
	const loopback = LOOPBACK.includes(parsed?.hostname);
	if (
		!origin ||
		parsed.hostname.includes('*') ||
		(parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && loopback)) ||
		origin !== rawOrigin.replace(/\/$/, '')
	) {
		throw new Error(
			'The extension origin must be one exact HTTPS origin, or an HTTP loopback origin for development.'
		);
	}
	return origin;
};

// Rewrites every origin-bearing manifest field; the extension code derives its
// origin from host_permissions at runtime, so no other file needs changes.
export const setOrigin = async (directory, rawOrigin) => {
	const origin = validateOrigin(rawOrigin);
	const path = resolve(directory, 'manifest.json');
	const manifest = JSON.parse(await readFile(path, 'utf8'));
	manifest.host_permissions = [`${origin}/*`];
	for (const script of manifest.content_scripts ?? []) script.matches = [`${origin}/*`];
	manifest.action = {
		...manifest.action,
		default_title: `Connect to your experiment at ${origin}`
	};
	await writeFile(path, `${JSON.stringify(manifest, null, '\t')}\n`);
	return origin;
};

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
	const [directory, origin] = process.argv.slice(2);
	if (!directory || !origin) {
		console.error('Usage: node set-origin.mjs <extension-dir> https://domain');
		process.exit(1);
	}
	try {
		console.log(
			`Set extension origin in ${resolve(directory)} to ${await setOrigin(directory, origin)}`
		);
		console.log('Reload the unpacked extension in chrome://extensions to activate this origin.');
	} catch (error) {
		console.error(error.message);
		process.exit(1);
	}
}
