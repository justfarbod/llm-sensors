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
