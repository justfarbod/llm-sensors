// This only checks the bridge in this tab. The server's current experiment
// response remains authoritative for whether the participant can start.
export const checkExperimentExtension = (
	sessionId: string,
	signal?: AbortSignal,
	timeoutMs = 3000
) =>
	new Promise<boolean>((resolve) => {
		if (signal?.aborted) return resolve(false);
		const requestId = crypto.randomUUID();
		const finish = (ready: boolean) => {
			clearTimeout(timer);
			window.removeEventListener('open-webui-experiment-extension-check-result', response);
			signal?.removeEventListener('abort', abort);
			resolve(ready);
		};
		const abort = () => finish(false);
		const response = (event: Event) => {
			const detail = (event as CustomEvent).detail;
			if (detail?.request_id !== requestId || detail?.experiment_session_id !== sessionId) return;
			finish(detail.ready === true);
		};
		const timer = setTimeout(abort, timeoutMs);
		signal?.addEventListener('abort', abort, { once: true });
		window.addEventListener('open-webui-experiment-extension-check-result', response);
		window.dispatchEvent(
			new CustomEvent('open-webui-experiment-extension-check-request', {
				detail: { request_id: requestId, experiment_session_id: sessionId }
			})
		);
	});

export const flushExperimentTelemetry = (final = false, timeoutMs = 12000) =>
	new Promise<boolean>((resolve) => {
		const requestId = crypto.randomUUID();
		let settled = false;
		const finish = (ok: boolean) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			window.removeEventListener('open-webui-experiment-telemetry-flush-result', response);
			resolve(ok);
		};
		const response = (event: Event) => {
			const detail = (event as CustomEvent).detail;
			if (detail?.request_id === requestId) finish(Boolean(detail.ok));
		};
		const timer = setTimeout(() => finish(false), timeoutMs);
		window.addEventListener('open-webui-experiment-telemetry-flush-result', response);
		window.dispatchEvent(
			new CustomEvent('open-webui-experiment-telemetry-flush-request', {
				detail: { request_id: requestId, final }
			})
		);
	});
