import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { flushExperimentTelemetry } from './experimentTelemetry';

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
