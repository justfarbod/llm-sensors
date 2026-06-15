const form = document.getElementById('form');
const input = document.getElementById('origin');
const status = document.getElementById('status');

chrome.storage.local.get('appOrigin').then(({ appOrigin }) => {
	input.value = appOrigin ?? '';
});

form.addEventListener('submit', async (event) => {
	event.preventDefault();
	status.textContent = '';
	try {
		const origin = new URL(input.value).origin;
		if (!['http:', 'https:'].includes(new URL(origin).protocol))
			throw new Error('Use an HTTP(S) origin.');
		const previous = (await chrome.storage.local.get('appOrigin')).appOrigin;
		const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
		if (!granted) throw new Error('Origin permission was not granted.');
		await chrome.storage.local.set({ appOrigin: origin });
		const result = await chrome.runtime.sendMessage({ type: 'configure-origin', origin });
		if (!result?.ok) throw new Error(result?.error ?? 'Could not configure content script.');
		if (previous && previous !== origin) {
			await chrome.permissions.remove({ origins: [`${previous}/*`] });
			await chrome.storage.local.remove('telemetryQueue');
		}
		input.value = origin;
		status.textContent = 'Saved. Reload an Open WebUI tab on this origin.';
	} catch (error) {
		status.textContent = String(error);
	}
});
