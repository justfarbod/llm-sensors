import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { checkExperimentExtension, flushExperimentTelemetry } from './experimentTelemetry';

describe('experiment extension connection handshake', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		vi.stubGlobal('window', new EventTarget());
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	it('accepts only a response for the requested check and experiment session', async () => {
		let request: any;
		window.addEventListener('open-webui-experiment-extension-check-request', (event) => {
			request = (event as CustomEvent).detail;
		});
		const settled = vi.fn();
		const checking = checkExperimentExtension('session-1').then(settled);
		const reply = (detail: object) =>
			window.dispatchEvent(
				new CustomEvent('open-webui-experiment-extension-check-result', { detail })
			);
		reply({ ...request, request_id: 'another-request', ready: true });
		reply({ ...request, experiment_session_id: 'another-session', ready: true });
		await vi.advanceTimersByTimeAsync(0);
		expect(settled).not.toHaveBeenCalled();
		reply({ ...request, ready: true });
		await checking;
		expect(settled).toHaveBeenCalledWith(true);
		expect(vi.getTimerCount()).toBe(0);
	});

	it('reports rejection immediately and times out missing extensions after three seconds', async () => {
		const missing = checkExperimentExtension('session-1');
		await vi.advanceTimersByTimeAsync(3000);
		await expect(missing).resolves.toBe(false);
		window.addEventListener('open-webui-experiment-extension-check-request', (event) => {
			window.dispatchEvent(
				new CustomEvent('open-webui-experiment-extension-check-result', {
					detail: { ...(event as CustomEvent).detail, ready: false }
				})
			);
		});
		await expect(checkExperimentExtension('session-1')).resolves.toBe(false);
	});

	it('cleans up a pending check when the gate closes or the session changes', async () => {
		const remove = vi.spyOn(window, 'removeEventListener');
		const controller = new AbortController();
		const checking = checkExperimentExtension('session-1', controller.signal);
		controller.abort();
		await expect(checking).resolves.toBe(false);
		expect(remove).toHaveBeenCalledWith(
			'open-webui-experiment-extension-check-result',
			expect.any(Function)
		);
		expect(vi.getTimerCount()).toBe(0);
	});
});

describe('experiment telemetry flush handshake', () => {
	beforeEach(() => {
		vi.stubGlobal('window', new EventTarget());
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	it('waits for the extension response with the matching request ID', async () => {
		window.addEventListener('open-webui-experiment-telemetry-flush-request', (event) => {
			const request = event as CustomEvent;
			expect(request.detail.final).toBe(true);
			window.dispatchEvent(
				new CustomEvent('open-webui-experiment-telemetry-flush-result', {
					detail: { request_id: request.detail.request_id, ok: true }
				})
			);
		});

		await expect(flushExperimentTelemetry(true)).resolves.toBe(true);
	});

	it('fails closed if no extension answers before the timeout', async () => {
		vi.useFakeTimers();
		const result = flushExperimentTelemetry(false, 100);
		await vi.advanceTimersByTimeAsync(100);
		await expect(result).resolves.toBe(false);
	});
});
