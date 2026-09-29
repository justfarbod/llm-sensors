// The deployment origin lives only in manifest.json, so a built folder can be
// retargeted with set-origin.mjs without rebuilding or editing this file.
globalThis.OPEN_WEBUI_EXPERIMENT_EXTENSION_CONFIG = Object.freeze({
	origin: new URL(chrome.runtime.getManifest().host_permissions[0].replace(/\*$/, '')).origin,
	schemaVersion: 4
});
