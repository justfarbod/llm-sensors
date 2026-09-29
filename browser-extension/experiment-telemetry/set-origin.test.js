import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { setOrigin } from './set-origin.mjs';

describe('set-origin', () => {
	let directory;
	const manifest = async () => JSON.parse(await readFile(join(directory, 'manifest.json'), 'utf8'));

	beforeEach(async () => {
		directory = await mkdtemp(join(tmpdir(), 'experiment-extension-'));
		await writeFile(
			join(directory, 'manifest.json'),
			await readFile(new URL('./manifest.json', import.meta.url), 'utf8')
		);
	});
	afterEach(() => rm(directory, { recursive: true, force: true }));

	it('rewrites every origin-bearing field of a built manifest', async () => {
		await setOrigin(directory, 'https://research.example.edu/');
		const result = await manifest();
		expect(result.host_permissions).toEqual(['https://research.example.edu/*']);
		expect(result.content_scripts[0].matches).toEqual(['https://research.example.edu/*']);
		expect(result.content_scripts[0].js).toEqual(['deployment-config.js', 'content.js']);
		expect(result.action.default_title).toBe(
			'Connect to your experiment at https://research.example.edu'
		);
		expect(result.name).toBe('Open WebUI Experiment Telemetry');
		expect(JSON.stringify(result)).not.toContain('localhost');
	});

	it('can retarget an already retargeted folder, including loopback development origins', async () => {
		await setOrigin(directory, 'https://first.test');
		await setOrigin(directory, 'http://[::1]:5173');
		expect((await manifest()).host_permissions).toEqual(['http://[::1]:5173/*']);
	});

	it.each([
		'http://research.example.edu',
		'https://research.example.edu/path',
		'https://*.example.edu',
		'not an origin'
	])('rejects %s without touching the manifest', async (origin) => {
		const before = await readFile(join(directory, 'manifest.json'), 'utf8');
		await expect(setOrigin(directory, origin)).rejects.toThrow('exact HTTPS origin');
		expect(await readFile(join(directory, 'manifest.json'), 'utf8')).toBe(before);
	});
});
