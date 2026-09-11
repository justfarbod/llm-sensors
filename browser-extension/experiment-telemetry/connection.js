(() => {
	const matchesOrigin = (tab, origin) => {
		try {
			return tab.id !== undefined && !tab.incognito && new URL(tab.url).origin === origin;
		} catch {
			return false;
		}
	};
	const isAdmin = (tab) => /^\/admin(\/|$)/.test(new URL(tab.url).pathname);
	const selectTab = (clickedTab, tabs, origin) => {
		if (matchesOrigin(clickedTab, origin)) return clickedTab;
		return tabs
			.filter((tab) => matchesOrigin(tab, origin))
			.sort(
				(a, b) =>
					Number(isAdmin(a)) - Number(isAdmin(b)) ||
					Number(b.windowId === clickedTab.windowId) - Number(a.windowId === clickedTab.windowId) ||
					(b.lastAccessed ?? 0) - (a.lastAccessed ?? 0) ||
					a.id - b.id
			)[0];
	};
	const reconnect = async (tabId) => {
		try {
			const response = await chrome.tabs.sendMessage(tabId, { type: 'extension-reconnect' });
			if (response?.ok) return;
		} catch {
			// An already-open page may not have a live content script yet.
		}
		await chrome.scripting.executeScript({
			target: { tabId },
			files: ['deployment-config.js', 'content.js']
		});
	};
	const openExperiment = async (clickedTab, origin) => {
		const tabs = matchesOrigin(clickedTab, origin)
			? []
			: await chrome.tabs.query({ url: `${origin}/*` });
		const tab = selectTab(clickedTab, tabs, origin);
		if (!tab) {
			await chrome.tabs.create({ url: origin });
			return;
		}
		await chrome.tabs.update(tab.id, { active: true });
		await chrome.windows.update(tab.windowId, { focused: true });
		await reconnect(tab.id);
	};
	globalThis.ExperimentExtensionConnection = {
		matchesOrigin,
		selectTab,
		reconnect,
		openExperiment
	};
})();
