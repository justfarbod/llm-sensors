import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import './connection.js';

const { openExperiment, selectTab } = globalThis.ExperimentExtensionConnection;
const origin = 'https://research.test';
const tab = (id, extra = {}) => ({
	id,
	windowId: 1,
	url: `${origin}/c/session?task=2#answer`,
	incognito: false,
	...extra
});

describe('extension toolbar connection', () => {
	beforeEach(() => {
		vi.stubGlobal('chrome', {
			tabs: {
				query: vi.fn().mockResolvedValue([]),
				update: vi.fn().mockResolvedValue(undefined),
				create: vi.fn().mockResolvedValue(undefined),
				sendMessage: vi.fn().mockResolvedValue({ ok: true })
			},
			windows: { update: vi.fn().mockResolvedValue(undefined) },
			scripting: { executeScript: vi.fn().mockResolvedValue([]) }
		});
	});
	afterEach(() => vi.unstubAllGlobals());

	it('reconnects the clicked experiment tab without navigating, reloading, or duplicating it', async () => {
		await openExperiment(tab(8), origin);
		expect(chrome.tabs.update).toHaveBeenCalledWith(8, { active: true });
		expect(chrome.tabs.sendMessage).toHaveBeenCalledWith(8, { type: 'extension-reconnect' });
		expect(chrome.tabs.create).not.toHaveBeenCalled();
		expect(chrome.tabs.query).not.toHaveBeenCalled();
		expect(chrome.scripting.executeScript).not.toHaveBeenCalled();
	});

	it('prefers participant tabs in the same window, then most recently accessed', () => {
		const clicked = tab(1, { url: 'https://elsewhere.test' });
		const candidates = [
			tab(2, { windowId: 2, lastAccessed: 100 }),
			tab(3, { lastAccessed: 10 }),
			tab(4, { lastAccessed: 20 }),
			tab(5, { url: `${origin}/admin`, lastAccessed: 200 }),
			tab(6, { incognito: true, lastAccessed: 300 }),
			tab(7, { url: 'https://research.test.attacker.test', lastAccessed: 400 })
		];
		expect(selectTab(clicked, candidates, origin).id).toBe(4);
		expect(selectTab({ ...clicked, windowId: 9 }, candidates, origin).id).toBe(2);
	});

	it('focuses a matching tab in another window and injects only when messaging fails', async () => {
		chrome.tabs.query.mockResolvedValue([tab(8, { windowId: 2 })]);
		chrome.tabs.sendMessage.mockRejectedValue(new Error('Receiving end does not exist'));
		await openExperiment(tab(1, { url: 'https://elsewhere.test' }), origin);
		expect(chrome.windows.update).toHaveBeenCalledWith(2, { focused: true });
		expect(chrome.scripting.executeScript).toHaveBeenCalledWith({
			target: { tabId: 8 },
			files: ['deployment-config.js', 'content.js']
		});
		expect(chrome.tabs.create).not.toHaveBeenCalled();
	});

	it('creates a tab only when no normal tab matches the exact deployment origin', async () => {
		chrome.tabs.query.mockResolvedValue([tab(2, { incognito: true })]);
		await openExperiment(tab(1, { url: 'https://elsewhere.test' }), origin);
		expect(chrome.tabs.create).toHaveBeenCalledTimes(1);
		expect(chrome.tabs.create).toHaveBeenCalledWith({ url: origin });
	});

	it('does not create another session tab when reconnection fails', async () => {
		chrome.tabs.sendMessage.mockRejectedValue(new Error('No receiver'));
		chrome.scripting.executeScript.mockRejectedValue(new Error('Site access denied'));
		await expect(openExperiment(tab(8), origin)).rejects.toThrow('Site access denied');
		expect(chrome.tabs.create).not.toHaveBeenCalled();
	});
});
