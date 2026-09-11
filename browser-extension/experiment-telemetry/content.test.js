import { readFileSync } from 'node:fs';
import { createContext, runInContext } from 'node:vm';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./content.js', import.meta.url), 'utf8');
const origin = 'https://research.test';

describe('extension content connection lifecycle', () => {
	let context;
	let runtimeListeners;
	let presence;
	const drain = () => vi.advanceTimersByTimeAsync(0);
	const state = (value) =>
		context.window.dispatchEvent(
			new CustomEvent('open-webui-experiment-state', {
				detail: { state: value, session_id: 'session-1' }
			})
		);
	const probe = (requestId = 'request-1') =>
		context.window.dispatchEvent(
			new CustomEvent('open-webui-experiment-extension-check-request', {
				detail: { request_id: requestId, experiment_session_id: 'session-1' }
			})
		);
	const messages = (type) =>
		context.chrome.runtime.sendMessage.mock.calls.filter(([message]) => message.type === type);

	beforeEach(() => {
		vi.useFakeTimers();
		runtimeListeners = new Set();
		presence = { ready: true, experiment_session_id: 'session-1', state: 'TASK_REQUIRED' };
		context = createContext({
			OPEN_WEBUI_EXPERIMENT_EXTENSION_CONFIG: { origin, schemaVersion: 2 },
			window: new EventTarget(),
			document: Object.assign(new EventTarget(), { hidden: false, hasFocus: () => true }),
			location: { origin, pathname: '/auth' },
			localStorage: { getItem: vi.fn().mockReturnValue('token-1') },
			chrome: {
				runtime: {
					id: 'extension-1',
					sendMessage: vi.fn().mockResolvedValue({ ok: true }),
					onMessage: {
						addListener: (listener) => runtimeListeners.add(listener),
						removeListener: (listener) => runtimeListeners.delete(listener)
					}
				}
			},
			fetch: vi.fn(async (url) => ({
				ok: true,
				json: async () =>
					url.endsWith('/heartbeat')
						? presence
						: {
								enabled: true,
								state: 'IN_PROGRESS',
								experiment_session_id: 'session-1'
							}
			})),
			AbortController,
			CustomEvent,
			crypto,
			setTimeout,
			clearTimeout,
			setInterval,
			clearInterval
		});
		runInContext(source, context);
	});
	afterEach(() => {
		context.__openWebUIExperimentTelemetry.dispose();
		vi.useRealTimers();
	});

	it('connects after client-side sign-in without collecting before Start', async () => {
		await drain();
		expect(context.fetch).not.toHaveBeenCalled();
		context.location.pathname = '/';
		state('TASK_REQUIRED');
		await drain();
		const response = vi.fn();
		context.window.addEventListener('open-webui-experiment-extension-check-result', response);
		probe();
		await drain();
		expect(response.mock.calls[0][0].detail).toEqual({
			request_id: 'request-1',
			experiment_session_id: 'session-1',
			ready: true
		});
		expect(messages('session-start')).toHaveLength(0);
		expect(messages('enqueue-events')).toHaveLength(0);
		presence.state = 'IN_PROGRESS';
		state('IN_PROGRESS');
		await drain();
		expect(messages('session-start')).toHaveLength(1);
	});

	it('coalesces probes, focus events and reinjection without duplicating listeners or timers', async () => {
		await drain();
		context.location.pathname = '/';
		const response = vi.fn();
		context.window.addEventListener('open-webui-experiment-extension-check-result', response);
		probe('one');
		probe('two');
		context.window.dispatchEvent(new Event('focus'));
		runInContext(source, context);
		await drain();
		expect(context.fetch).toHaveBeenCalledTimes(1);
		expect(response).toHaveBeenCalledTimes(2);
		expect(runtimeListeners.size).toBe(1);
		expect(vi.getTimerCount()).toBe(1);
	});

	it('does not turn repeated server-state refreshes into a heartbeat loop', async () => {
		context.location.pathname = '/';
		state('TASK_REQUIRED');
		await drain();
		const count = context.fetch.mock.calls.length;
		for (let i = 0; i < 10; i += 1) state('TASK_REQUIRED');
		await drain();
		expect(context.fetch).toHaveBeenCalledTimes(count);
	});

	it.each(['/auth', '/admin', '/admin/analytics'])(
		'does not check or record on %s',
		async (pathname) => {
			context.location.pathname = pathname;
			probe();
			await drain();
			expect(context.fetch).not.toHaveBeenCalled();
			expect(messages('session-start')).toHaveLength(0);
		}
	);

	it('reports rejected capabilities and recovers after a failed network request', async () => {
		await drain();
		context.location.pathname = '/';
		const response = vi.fn();
		context.window.addEventListener('open-webui-experiment-extension-check-result', response);
		context.fetch.mockRejectedValueOnce(new Error('offline'));
		probe();
		await drain();
		expect(response.mock.calls.at(-1)[0].detail.ready).toBe(false);
		presence.ready = false;
		probe();
		await drain();
		expect(response.mock.calls.at(-1)[0].detail.ready).toBe(false);
		presence.ready = true;
		probe();
		await drain();
		expect(response.mock.calls.at(-1)[0].detail.ready).toBe(true);
	});

	it('ignores an in-flight heartbeat after sign-out', async () => {
		await drain();
		context.location.pathname = '/';
		let resolveFetch;
		context.fetch.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					resolveFetch = resolve;
				})
		);
		probe();
		await drain();
		state('SIGNED_OUT');
		resolveFetch({ ok: true, json: async () => ({ ...presence, state: 'IN_PROGRESS' }) });
		await drain();
		expect(messages('session-start')).toHaveLength(0);
	});

	it('times out a stuck heartbeat and allows a subsequent check to recover', async () => {
		await drain();
		context.location.pathname = '/';
		context.fetch.mockImplementationOnce(
			(_url, { signal }) =>
				new Promise((_resolve, reject) => {
					signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
				})
		);
		const response = vi.fn();
		context.window.addEventListener('open-webui-experiment-extension-check-result', response);
		probe();
		await vi.advanceTimersByTimeAsync(5000);
		expect(response.mock.calls.at(-1)[0].detail.ready).toBe(false);
		probe();
		await drain();
		expect(response.mock.calls.at(-1)[0].detail.ready).toBe(true);
	});

	it('does not duplicate recording after reinjection or record when navigating back to auth', async () => {
		await drain();
		context.location.pathname = '/';
		presence.state = 'IN_PROGRESS';
		state('IN_PROGRESS');
		await drain();
		runInContext(source, context);
		await drain();
		context.window.dispatchEvent(new Event('blur'));
		await drain();
		const events = messages('enqueue-events').flatMap(([message]) => message.events);
		expect(events.filter((event) => event.type === 'window_blur')).toHaveLength(1);
		expect(events.filter((event) => event.type === 'focus_away')).toHaveLength(1);
		context.chrome.runtime.sendMessage.mockClear();
		context.location.pathname = '/auth';
		context.window.dispatchEvent(new Event('blur'));
		await drain();
		expect(messages('enqueue-events')).toHaveLength(0);
	});

	it("does not stop another tab's recording from an unauthenticated page", async () => {
		await drain();
		context.location.pathname = '/';
		context.localStorage.getItem.mockReturnValue(null);
		probe();
		await drain();
		expect(context.fetch).not.toHaveBeenCalled();
		expect(messages('session-stop')).toHaveLength(0);
	});

	it('removes obsolete listeners and timers when the extension context is invalidated', async () => {
		await drain();
		context.location.pathname = '/';
		context.chrome.runtime.id = undefined;
		probe();
		await drain();
		expect(runtimeListeners.size).toBe(0);
		expect(vi.getTimerCount()).toBe(0);
		expect(context.__openWebUIExperimentTelemetry.isAlive()).toBe(false);
	});
});
