import { readFileSync } from 'node:fs';
import { createContext, runInContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const origin = 'http://localhost:5173';
const event = () => {
	const listeners = [];
	return {
		addListener: (listener) => listeners.push(listener),
		fire: () => listeners.forEach((listener) => listener())
	};
};
const harness = (tabs) => {
	const chrome = {
		runtime: { onInstalled: event(), onMessage: event() },
		action: { onClicked: event() },
		alarms: { onAlarm: event() },
		permissions: { onRemoved: event() },
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
	const context = createContext({
		chrome,
		URL,
		TextEncoder,
		console,
		OPEN_WEBUI_EXPERIMENT_EXTENSION_CONFIG: { origin, schemaVersion: 2 }
	});
	context.importScripts = (...files) => {
		for (const file of files) {
			if (file === 'deployment-config.js') continue;
			runInContext(readFileSync(new URL(file, import.meta.url), 'utf8'), context);
		}
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
});
