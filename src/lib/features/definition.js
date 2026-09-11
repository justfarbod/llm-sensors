// Shared by the Node build launcher and browser feature guards.
export const excludedRouteNames = ['notes', 'channels', 'calendar', 'automations', 'playground'];

export function resolveProfile(name = 'research') {
	if (!['research', 'full'].includes(name)) throw new Error(`Unknown frontend profile: ${name}`);
	return name;
}

/** @param {string} name */
export function profileFeatures(name) {
	const full = resolveProfile(name) === 'full';
	return Object.freeze({
		voice: full,
		python: full,
		terminals: full,
		overview: full,
		personal: full
	});
}

/**
 * Suppress stale execution preferences without changing saved settings or research metadata.
 * @param {string} name
 * @param {Record<string, any>} request
 */
export function constrainChatRequest(name, request) {
	if (resolveProfile(name) === 'full') return request;
	return {
		...request,
		terminal_id: undefined,
		features: { ...request.features, voice: false, code_interpreter: false }
	};
}

/** @param {string} name @param {(response: object) => void} [callback] */
export function rejectPythonExecution(name, callback) {
	if (resolveProfile(name) === 'full') return false;
	callback?.({
		stdout: null,
		stderr: 'Python execution is unavailable in the research frontend.',
		result: null
	});
	return true;
}
