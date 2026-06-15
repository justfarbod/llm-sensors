const SCRIPT_ID = 'open-webui-experiment-telemetry';

async function configureOrigin(origin) {
	const registrations = await chrome.scripting.getRegisteredContentScripts();
	if (registrations.some((script) => script.id === SCRIPT_ID)) {
		await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] });
	}
	if (!origin) return;
	await chrome.scripting.registerContentScripts([
		{
			id: SCRIPT_ID,
			matches: [`${origin}/*`],
			js: ['telemetry-buffer.js', 'content.js'],
			runAt: 'document_idle',
			persistAcrossSessions: true
		}
	]);
}

chrome.runtime.onInstalled.addListener(async () => {
	const { appOrigin } = await chrome.storage.local.get('appOrigin');
	await configureOrigin(appOrigin);
	if (!appOrigin) chrome.runtime.openOptionsPage();
});

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
	if (message?.type !== 'configure-origin') return;
	configureOrigin(message.origin)
		.then(() => sendResponse({ ok: true }))
		.catch((error) => sendResponse({ ok: false, error: String(error) }));
	return true;
});
