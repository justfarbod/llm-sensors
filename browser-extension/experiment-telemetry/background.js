importScripts('deployment-config.js', 'tab-normalization.js', 'connection.js');

const CONFIG = globalThis.OPEN_WEBUI_EXPERIMENT_EXTENSION_CONFIG;
const { normalizeTab } = globalThis.ExperimentTabTelemetry;
const STATE_KEY = 'experimentTelemetrySession';
const QUEUE_KEY = 'experimentTelemetryQueueV2';
const MAX_EVENTS = 10000;
const MAX_QUEUE_BYTES = 8 * 1024 * 1024;
const MAX_BATCH_BYTES = 220 * 1024;
const FLUSH_ALARM = 'experiment-telemetry-flush';
const HEARTBEAT_ALARM = 'experiment-telemetry-heartbeat-expiry';
const HEARTBEAT_TIMEOUT_MS = 45000;
const TAB_TYPES = new Set([
	'tab_snapshot',
	'tab_created',
	'tab_updated',
	'tab_activated',
	'tab_highlighted',
	'tab_moved',
	'tab_attached',
	'tab_detached',
	'tab_replaced',
	'tab_removed'
]);

let serial = Promise.resolve();
let flushTimer = null;

const runSerial = (work) => {
	serial = serial.then(work, work);
	return serial;
};
const now = () => new Date().toISOString();
const byteLength = (value) => new TextEncoder().encode(JSON.stringify(value)).byteLength;
const isConfiguredOrigin = (value) => {
	try {
		return new URL(value).origin === CONFIG.origin;
	} catch {
		return false;
	}
};
const getState = async () => (await chrome.storage.session.get(STATE_KEY))[STATE_KEY] ?? null;
const setState = async (state) => chrome.storage.session.set({ [STATE_KEY]: state });
const getQueue = async () =>
	(await chrome.storage.local.get(QUEUE_KEY))[QUEUE_KEY] ?? {
		sessionId: null,
		events: [],
		dropped: 0
	};
const setQueue = async (queue) => chrome.storage.local.set({ [QUEUE_KEY]: queue });

const nextEvent = async (type, fields = {}) => {
	const state = await getState();
	if (!state?.sessionId) return null;
	state.sequence += 1;
	await setState(state);
	return {
		event_id: crypto.randomUUID(),
		type,
		timestamp: now(),
		field: 'unknown',
		browser_session_id: state.browserSessionId,
		sequence: state.sequence,
		...fields
	};
};

const enforceQueueLimit = (queue) => {
	let dropped = 0;
	while (
		queue.events.length > MAX_EVENTS ||
		(queue.events.length && byteLength(queue) > MAX_QUEUE_BYTES)
	) {
		queue.events.shift();
		dropped += 1;
	}
	queue.dropped = (queue.dropped ?? 0) + dropped;
};

const enqueueUnlocked = async (events) => {
	const state = await getState();
	if (!state?.sessionId || !events.length) return;
	const queue = await getQueue();
	if (queue.sessionId !== state.sessionId) {
		queue.sessionId = state.sessionId;
		queue.events = [];
		queue.dropped = 0;
	}
	queue.events.push(...events);
	enforceQueueLimit(queue);
	await setQueue(queue);
	if (queue.events.length >= 50) void flush();
	else scheduleFlush();
};
const enqueue = (events) => runSerial(() => enqueueUnlocked(events.filter(Boolean)));

const tabEventUnlocked = async (type, tab, fields = {}) => {
	const normalized = normalizeTab(tab);
	if (!normalized) return;
	try {
		if ((await chrome.windows.get(normalized.window_id)).type !== 'normal') return;
	} catch {
		return;
	}
	const state = await getState();
	if (!state) return;
	state.tabCache[String(normalized.tab_id)] = normalized;
	await setState(state);
	await enqueueUnlocked([await nextEvent(type, { tab: normalized, ...fields })]);
};
const tabEvent = (type, tab, fields = {}) => runSerial(() => tabEventUnlocked(type, tab, fields));

const safeTab = async (tabId) => {
	try {
		return await chrome.tabs.get(tabId);
	} catch {
		return null;
	}
};

const snapshot = async (phase, excludedTabIds = new Set()) => {
	const tabs = await chrome.tabs.query({ windowType: 'normal' });
	for (const tab of tabs)
		if (!excludedTabIds.has(tab.id))
			await tabEventUnlocked('tab_snapshot', tab, { snapshot_phase: phase });
};

const makeBatch = (queue, state) => {
	const batch = [];
	let bytes = byteLength({
		experiment_session_id: state.sessionId,
		schema_version: CONFIG.schemaVersion,
		events: []
	});
	for (const event of queue.events.slice(0, 100)) {
		const eventBytes = byteLength(event) + 1;
		if (batch.length && bytes + eventBytes > MAX_BATCH_BYTES) break;
		batch.push(event);
		bytes += eventBytes;
	}
	return batch;
};

const fetchWithRetries = async (state, body) => {
	for (let attempt = 0; attempt < 3; attempt += 1) {
		try {
			const response = await fetch(`${state.origin}/api/v1/experiments/telemetry/events`, {
				method: 'POST',
				headers: {
					Accept: 'application/json',
					'Content-Type': 'application/json',
					authorization: `Bearer ${state.token}`
				},
				body,
				signal: AbortSignal.timeout(10000)
			});
			if (response.ok || [401, 403, 409, 422].includes(response.status)) return response;
		} catch {}
		await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
	}
	return null;
};

const flushUnlocked = async () => {
	const state = await getState();
	if (!state?.sessionId) return { ok: true, accepted: 0 };
	const queue = await getQueue();
	if (queue.sessionId !== state.sessionId) return { ok: true, accepted: 0 };
	if (queue.dropped) {
		const loss = await nextEvent('telemetry_loss', { dropped_event_count: queue.dropped });
		if (loss) queue.events.unshift(loss);
		queue.dropped = 0;
		await setQueue(queue);
	}
	let accepted = 0;
	while (queue.events.length) {
		const batch = makeBatch(queue, state);
		const response = await fetchWithRetries(
			state,
			JSON.stringify({
				experiment_session_id: state.sessionId,
				schema_version: CONFIG.schemaVersion,
				events: batch
			})
		);
		if (!response?.ok) {
			if (response && [401, 403, 409, 422].includes(response.status)) {
				await chrome.storage.session.remove(STATE_KEY);
				chrome.alarms.clear(FLUSH_ALARM);
				chrome.alarms.clear(HEARTBEAT_ALARM);
			} else chrome.alarms.create(FLUSH_ALARM, { delayInMinutes: 0.5 });
			return { ok: false, accepted };
		}
		queue.events.splice(0, batch.length);
		accepted += batch.length;
		await setQueue(queue);
	}
	return { ok: true, accepted };
};
const flush = () => runSerial(flushUnlocked);

const scheduleFlush = () => {
	if (flushTimer) return;
	flushTimer = setTimeout(() => {
		flushTimer = null;
		void flush();
	}, 1500);
};

const startUnlocked = async ({ origin, token, sessionId, appTabId }) => {
	if (origin !== CONFIG.origin) return { ok: false, error: 'Origin mismatch.' };
	let state = await getState();
	if (state?.sessionId !== sessionId) {
		const queue = await getQueue();
		const queuedForSession = queue.sessionId === sessionId ? queue.events : [];
		const queuedBrowserSessionId = queuedForSession.find(
			(event) => typeof event.browser_session_id === 'string'
		)?.browser_session_id;
		const queuedSequence = queuedForSession.reduce(
			(maximum, event) => Math.max(maximum, Number(event.sequence) || 0),
			0
		);
		if (queue.sessionId !== sessionId) await chrome.storage.local.remove(QUEUE_KEY);
		state = {
			origin,
			token,
			sessionId,
			browserSessionId: queuedBrowserSessionId ?? crypto.randomUUID(),
			sequence: queuedSequence,
			lastHeartbeat: Date.now(),
			appTabIds: appTabId === undefined ? [] : [appTabId],
			tabCache: {}
		};
		await setState(state);
		await snapshot('initial');
	} else {
		state.token = token;
		state.lastHeartbeat = Date.now();
		if (appTabId !== undefined && !state.appTabIds.includes(appTabId))
			state.appTabIds.push(appTabId);
		await setState(state);
	}
	chrome.alarms.create(FLUSH_ALARM, { periodInMinutes: 0.5 });
	chrome.alarms.create(HEARTBEAT_ALARM, { delayInMinutes: HEARTBEAT_TIMEOUT_MS / 60000 });
	return { ok: true, browser_session_id: state.browserSessionId };
};
const start = (data) => runSerial(() => startUnlocked(data));

async function stopUnlocked(finalSnapshot = true, clear = false, excludedTabIds = new Set()) {
	const state = await getState();
	if (!state) return { ok: true };
	if (finalSnapshot)
		try {
			await snapshot('final', excludedTabIds);
		} catch {}
	try {
		await flushUnlocked();
	} catch {}
	await Promise.allSettled(
		(state.appTabIds ?? []).map((tabId) =>
			chrome.tabs.sendMessage(tabId, { type: 'telemetry-stopped' })
		)
	);
	await chrome.storage.session.remove(STATE_KEY);
	if (clear) await chrome.storage.local.remove(QUEUE_KEY);
	chrome.alarms.clear(FLUSH_ALARM);
	chrome.alarms.clear(HEARTBEAT_ALARM);
	return { ok: true };
}
const stop = (finalSnapshot = true, clear = false) =>
	runSerial(() => stopUnlocked(finalSnapshot, clear));

let reconnectingTabs = null;
const reconnectOpenTabs = () => {
	if (reconnectingTabs) return reconnectingTabs;
	reconnectingTabs = (async () => {
		for (const tab of await chrome.tabs.query({ url: `${CONFIG.origin}/*` })) {
			if (!globalThis.ExperimentExtensionConnection.matchesOrigin(tab, CONFIG.origin)) continue;
			try {
				await globalThis.ExperimentExtensionConnection.reconnect(tab.id);
			} catch {}
		}
	})().finally(() => {
		reconnectingTabs = null;
	});
	return reconnectingTabs;
};
chrome.runtime.onInstalled.addListener(() => void reconnectOpenTabs().catch(() => {}));

// Reloading/re-enabling an extension can start a fresh worker without firing
// onInstalled. Repair already-open app tabs whenever this worker starts too.
// The content script and this sweep both coalesce duplicate connection checks.
void reconnectOpenTabs().catch(() => {});

chrome.action.onClicked.addListener((tab) => {
	void globalThis.ExperimentExtensionConnection.openExperiment(tab, CONFIG.origin).catch((error) =>
		console.warn('Could not reconnect the experiment tab.', error)
	);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
	if (message?.type === 'extension-capabilities') {
		Promise.all([
			chrome.permissions.contains({ permissions: ['tabs'] }),
			chrome.extension.isAllowedIncognitoAccess()
		]).then(([tabsPermission, incognitoAllowed]) =>
			sendResponse({
				extension_version: chrome.runtime.getManifest().version,
				extension_id: chrome.runtime.id,
				schema_version: CONFIG.schemaVersion,
				tabs_permission: tabsPermission,
				incognito_allowed: incognitoAllowed,
				origin: CONFIG.origin
			})
		);
		return true;
	}
	if (message?.type === 'session-start') {
		start({ ...message, appTabId: sender.tab?.id }).then(sendResponse);
		return true;
	}
	if (message?.type === 'enqueue-events') {
		enqueue(Array.isArray(message.events) ? message.events : []).then(() =>
			sendResponse({ ok: true })
		);
		return true;
	}
	if (message?.type === 'flush') {
		runSerial(async () => {
			if (message.final) await snapshot('final');
			return flushUnlocked();
		}).then(sendResponse);
		return true;
	}
	if (message?.type === 'session-stop') {
		stop(message.final !== false, Boolean(message.clear)).then(sendResponse);
		return true;
	}
});

chrome.alarms.onAlarm.addListener((alarm) => {
	if (![FLUSH_ALARM, HEARTBEAT_ALARM].includes(alarm.name)) return;
	void runSerial(async () => {
		const state = await getState();
		if (!state) return;
		if (alarm.name === HEARTBEAT_ALARM || Date.now() - state.lastHeartbeat >= HEARTBEAT_TIMEOUT_MS)
			await stopUnlocked(true, false);
		else await flushUnlocked();
	});
});

chrome.permissions.onRemoved.addListener(() => void stop(true, false));

chrome.tabs.onCreated.addListener((tab) => void tabEvent('tab_created', tab));
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
	void runSerial(async () => {
		const state = await getState();
		if (!state) return;
		if (state.appTabIds.includes(tabId) && tab.url && !isConfiguredOrigin(tab.url)) {
			state.appTabIds = state.appTabIds.filter((id) => id !== tabId);
			await setState(state);
			if (!state.appTabIds.length) await stopUnlocked(true, false, new Set([tabId]));
			return;
		}
		await tabEventUnlocked('tab_updated', tab, { changed_fields: Object.keys(changeInfo) });
	});
});
chrome.tabs.onActivated.addListener(
	({ tabId }) => void safeTab(tabId).then((tab) => tabEvent('tab_activated', tab))
);
chrome.tabs.onHighlighted.addListener(({ tabIds }) => {
	for (const tabId of tabIds) void safeTab(tabId).then((tab) => tabEvent('tab_highlighted', tab));
});
chrome.tabs.onMoved.addListener(
	(tabId, info) =>
		void safeTab(tabId).then((tab) =>
			tabEvent('tab_moved', tab, { from_index: info.fromIndex, to_index: info.toIndex })
		)
);
chrome.tabs.onAttached.addListener(
	(tabId, info) =>
		void safeTab(tabId).then((tab) =>
			tabEvent('tab_attached', tab, { new_window_id: info.newWindowId })
		)
);
chrome.tabs.onDetached.addListener((tabId, info) => {
	void runSerial(async () => {
		const state = await getState();
		const tab = state?.tabCache?.[String(tabId)];
		if (tab)
			await enqueueUnlocked([
				await nextEvent('tab_detached', { tab, old_window_id: info.oldWindowId })
			]);
	});
});
chrome.tabs.onReplaced.addListener(
	(addedTabId, removedTabId) =>
		void safeTab(addedTabId).then((tab) =>
			tabEvent('tab_replaced', tab, { replaced_tab_id: removedTabId })
		)
);
chrome.tabs.onRemoved.addListener((tabId, info) => {
	void runSerial(async () => {
		const state = await getState();
		if (!state) return;
		const tab = state.tabCache[String(tabId)];
		delete state.tabCache[String(tabId)];
		state.appTabIds = state.appTabIds.filter((id) => id !== tabId);
		await setState(state);
		if (tab)
			await enqueueUnlocked([
				await nextEvent('tab_removed', { tab, is_window_closing: info.isWindowClosing })
			]);
		if (!state.appTabIds.length) await stopUnlocked(true, false);
	});
});
chrome.windows.onFocusChanged.addListener((windowId) => {
	void runSerial(async () => {
		const state = await getState();
		if (!state) return;
		await enqueueUnlocked([
			await nextEvent('window_focus_changed', {
				window_id: windowId,
				window_focused: windowId !== chrome.windows.WINDOW_ID_NONE
			})
		]);
	});
});

globalThis.ExperimentTelemetryBackground = { normalizeTab, TAB_TYPES };
