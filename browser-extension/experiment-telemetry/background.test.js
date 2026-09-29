import { readFileSync } from 'node:fs';
import { createContext, runInContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const origin = 'http://localhost:5173';
const event = () => {
	const listeners = [];
	return {
		addListener: (listener) => listeners.push(listener),
		fire: (...args) => listeners.map((listener) => listener(...args))
	};
};
const harness = (tabs) => {
	const chrome = {
		runtime: {
			id: 'extension-1',
			getManifest: () => ({
				name: 'Open WebUI Experiment Telemetry',
				version: '2.2.0',
				host_permissions: [`${origin}/*`]
			}),
			onInstalled: event(),
			onMessage: event()
		},
		extension: { isAllowedIncognitoAccess: vi.fn().mockResolvedValue(false) },
		permissions: { onRemoved: event(), contains: vi.fn().mockResolvedValue(true) },
		action: { onClicked: event() },
		alarms: { onAlarm: event() },
		windows: { onFocusChanged: event() },
		tabs: {
			...Object.fromEntries(
				[
					'onCreated',
					'onUpdated',
					'onActivated',
					'onHighlighted',
					'onMoved',
					'onAttached',
					'onDetached',
					'onReplaced',
					'onRemoved'
				].map((name) => [name, event()])
			),
			query: vi.fn().mockResolvedValue(tabs),
			sendMessage: vi.fn().mockRejectedValue(new Error('No receiver')),
			create: vi.fn()
		},
		scripting: { executeScript: vi.fn().mockResolvedValue([]) }
	};
	const context = createContext({ chrome, URL, TextEncoder, console });
	context.importScripts = (...files) => {
		for (const file of files)
			runInContext(readFileSync(new URL(file, import.meta.url), 'utf8'), context);
	};
	runInContext(readFileSync(new URL('./background.js', import.meta.url), 'utf8'), context);
	return chrome;
};

describe('extension worker connection recovery', () => {
	it('repairs already-open matching tabs on worker startup without requiring onInstalled', async () => {
		const chrome = harness([
			{ id: 1, url: `${origin}/c/current?task=2`, incognito: false },
			{ id: 2, url: 'http://localhost:8080/', incognito: false },
			{ id: 3, url: `${origin}/`, incognito: true }
		]);
		await vi.waitFor(() => expect(chrome.scripting.executeScript).toHaveBeenCalledTimes(1));
		expect(chrome.scripting.executeScript).toHaveBeenCalledWith({
			target: { tabId: 1 },
			files: ['deployment-config.js', 'content.js']
		});
		expect(chrome.tabs.create).not.toHaveBeenCalled();
	});

	it('coalesces startup and installation sweeps and continues past inaccessible tabs', async () => {
		const chrome = harness([1, 2].map((id) => ({ id, url: `${origin}/`, incognito: false })));
		chrome.scripting.executeScript.mockRejectedValueOnce(new Error('Tab closed'));
		chrome.runtime.onInstalled.fire();
		await vi.waitFor(() => expect(chrome.scripting.executeScript).toHaveBeenCalledTimes(2));
		expect(chrome.tabs.query).toHaveBeenCalledTimes(1);
		expect(chrome.tabs.create).not.toHaveBeenCalled();
	});

	it('reports the manifest origin, name, and identity in its capabilities', async () => {
		const chrome = harness([]);
		const response = await new Promise((resolve) =>
			chrome.runtime.onMessage.fire({ type: 'extension-capabilities' }, {}, resolve)
		);
		expect(response).toEqual({
			extension_version: '2.2.0',
			extension_id: 'extension-1',
			extension_name: 'Open WebUI Experiment Telemetry',
			schema_version: 4,
			tabs_permission: true,
			incognito_allowed: false,
			origin
		});
	});
});
