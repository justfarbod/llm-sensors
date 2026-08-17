(() => {
	if (globalThis.__openWebUIExperimentTelemetryLoaded) return;
	globalThis.__openWebUIExperimentTelemetryLoaded = true;
	if (location.pathname.startsWith('/auth') || location.pathname.startsWith('/admin')) return;

	const CONFIG = globalThis.OPEN_WEBUI_EXPERIMENT_EXTENSION_CONFIG;
	if (!CONFIG || location.origin !== CONFIG.origin) return;

	let enabled = false;
	let sessionId = null;
	let statusTimer = null;
	let eventTimer = null;
	let pendingEvents = [];
	let awayStartedAt = null;
	let lastKeydown = null;
	const pendingHolds = new Map();
	const removers = [];

	const id = () => crypto.randomUUID();
	const eventBase = (type, context = 'unknown') => ({
		event_id: id(),
		type,
		timestamp: new Date().toISOString(),
		...(typeof context === 'string' ? { field: context } : context)
	});

	const fieldFor = (target) => {
		const element = target instanceof Element ? target : target?.parentElement;
		const tagged = element?.closest('[data-experiment-field]');
		const field = tagged?.getAttribute('data-experiment-field');
		if (!['essay', 'question', 'chat'].includes(field)) return null;
		const input = element?.closest('input');
		if (input?.type === 'password') return null;
		return {
			field,
			...(tagged?.getAttribute('data-session-task-id')
				? { session_task_id: tagged.getAttribute('data-session-task-id') }
				: {}),
			...(field === 'question'
				? {
						question_id: tagged.getAttribute('data-question-id'),
						submission_id: tagged.getAttribute('data-submission-id') || undefined
					}
				: {})
		};
	};

	const keyClass = (event) => {
		const value = event.key;
		if (['Control', 'Shift', 'Alt', 'Meta', 'AltGraph'].includes(value)) return 'modifier';
		if ((event.ctrlKey || event.metaKey || event.altKey) && value !== 'AltGraph') return 'shortcut';
		if (value === 'Backspace') return 'backspace';
		if (value === 'Delete') return 'delete';
		if (value === 'Enter') return 'enter';
		if ([' ', 'Tab'].includes(value)) return 'whitespace';
		if (
			value.startsWith('Arrow') ||
			['Home', 'End', 'PageUp', 'PageDown', 'Insert', 'Escape'].includes(value)
		)
			return 'arrow/navigation';
		if (value.length === 1) return 'printable';
		return 'other';
	};

	const modifiers = (event) => ({
		ctrl: event.ctrlKey,
		shift: event.shiftKey,
		alt: event.altKey,
		meta: event.metaKey
	});
	const listen = (target, type, handler, options) => {
		target.addEventListener(type, handler, options);
		removers.push(() => target.removeEventListener(type, handler, options));
	};

	const queue = (event) => {
		pendingEvents.push(event);
		if (pendingEvents.length >= 50) void sendPending();
		else if (!eventTimer) {
			eventTimer = setTimeout(() => {
				eventTimer = null;
				void sendPending();
			}, 1000);
		}
	};
	const finalizePendingHolds = () => {
		const finishedAt = Date.now();
		for (const holds of pendingHolds.values())
			for (const hold of holds)
				queue({
					...hold.event,
					hold_duration_ms: Math.min(finishedAt - hold.startedAt, 600000)
				});
		pendingHolds.clear();
	};

	const sendPending = async () => {
		if (!pendingEvents.length) return;
		const events = pendingEvents;
		pendingEvents = [];
		try {
			await chrome.runtime.sendMessage({ type: 'enqueue-events', events });
		} catch {
			pendingEvents = [...events, ...pendingEvents].slice(-500);
		}
	};

	const selectedMetadata = (event) => {
		let value = '';
		const target = event.target;
		if (
			(target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) &&
			target.selectionStart !== null &&
			target.selectionEnd !== null &&
			target.selectionStart !== target.selectionEnd
		)
			value = target.value.slice(target.selectionStart, target.selectionEnd);
		else value = window.getSelection()?.toString() ?? '';
		return { text_length: value.length, line_count: value ? value.split(/\r\n|\r|\n/).length : 0 };
	};
	const clipboardMetadata = (event) => {
		const value = event.clipboardData?.getData('text/plain') ?? '';
		return { text_length: value.length, line_count: value ? value.split(/\r\n|\r|\n/).length : 0 };
	};

	const markAway = () => {
		if (awayStartedAt !== null) return;
		awayStartedAt = Date.now();
		lastKeydown = null;
		queue(eventBase('focus_away'));
	};
	const markReturn = () => {
		if (awayStartedAt === null || document.hidden || !document.hasFocus()) return;
		queue({ ...eventBase('focus_return'), away_duration_ms: Date.now() - awayStartedAt });
		awayStartedAt = null;
	};

	const attach = () => {
		listen(
			document,
			'keydown',
			(event) => {
				const context = fieldFor(event.target);
				if (!context || event.repeat || event.isComposing) return;
				const classification = keyClass(event);
				const timestamp = Date.now();
				const contextKey = `${context.field}:${context.session_task_id ?? ''}:${context.question_id ?? ''}`;
				const telemetryEvent = {
					...eventBase('keystroke', context),
					key_class: classification,
					modifiers: modifiers(event)
				};
				if (lastKeydown?.contextKey === contextKey)
					telemetryEvent.inter_key_interval_ms = timestamp - lastKeydown.timestamp;
				lastKeydown = { contextKey, timestamp };
				const signature = `${contextKey}:${classification}`;
				const holds = pendingHolds.get(signature) ?? [];
				holds.push({ event: telemetryEvent, startedAt: timestamp });
				if (holds.length > 10) {
					const oldest = holds.shift();
					queue({
						...oldest.event,
						hold_duration_ms: Math.min(timestamp - oldest.startedAt, 600000)
					});
				}
				pendingHolds.set(signature, holds);
			},
			true
		);
		listen(
			document,
			'keyup',
			(event) => {
				const context = fieldFor(event.target);
				if (!context || event.isComposing) return;
				const contextKey = `${context.field}:${context.session_task_id ?? ''}:${context.question_id ?? ''}`;
				const signature = `${contextKey}:${keyClass(event)}`;
				const holds = pendingHolds.get(signature);
				const hold = holds?.shift();
				if (!hold) return;
				queue({
					...hold.event,
					hold_duration_ms: Math.min(Date.now() - hold.startedAt, 600000)
				});
				if (!holds.length) pendingHolds.delete(signature);
			},
			true
		);
		for (const type of ['copy', 'cut', 'paste'])
			listen(
				document,
				type,
				(event) => {
					const context = fieldFor(event.target);
					if (!context) return;
					queue({
						...eventBase(type, context),
						...(type === 'paste' ? clipboardMetadata(event) : selectedMetadata(event))
					});
				},
				true
			);
		listen(
			document,
			'change',
			(event) => {
				const context = fieldFor(event.target);
				if (!context || context.field !== 'question') return;
				const target = event.target;
				const controlType = target?.getAttribute?.('data-question-control');
				if (!['single_choice', 'multiple_select', 'fill_blank', 'free_text'].includes(controlType))
					return;
				const root = target.closest('[data-experiment-field="question"]');
				const controls = Array.from(
					root?.querySelectorAll(`[data-question-control="${controlType}"]`) ?? []
				);
				const answered = controls.some((control) =>
					control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement
						? control.type === 'checkbox' || control.type === 'radio'
							? control.checked
							: Boolean(control.value.trim())
						: false
				);
				queue({ ...eventBase('answer_change', context), control_type: controlType, answered });
			},
			true
		);
		listen(document, 'visibilitychange', () => {
			queue({
				...eventBase('visibility_change'),
				visibility: document.hidden ? 'hidden' : 'visible'
			});
			document.hidden ? markAway() : markReturn();
			if (document.hidden) {
				finalizePendingHolds();
				void flush(false);
			}
		});
		listen(window, 'blur', () => {
			queue(eventBase('window_blur'));
			finalizePendingHolds();
			markAway();
			void flush(false);
		});
		listen(window, 'focus', () => {
			queue(eventBase('window_focus'));
			markReturn();
			void checkStatus();
		});
		listen(window, 'pagehide', () => void flush(false));
	};

	const detach = () => {
		while (removers.length) removers.pop()();
		finalizePendingHolds();
		lastKeydown = null;
	};

	const heartbeat = async () => {
		const token = localStorage.getItem('token');
		if (!token) return null;
		const capabilities = await chrome.runtime.sendMessage({ type: 'extension-capabilities' });
		const response = await fetch(
			`${CONFIG.origin}/api/v1/experiments/telemetry/extension/heartbeat`,
			{
				method: 'POST',
				headers: {
					Accept: 'application/json',
					'Content-Type': 'application/json',
					authorization: `Bearer ${token}`
				},
				body: JSON.stringify(capabilities)
			}
		);
		return response.ok ? response.json() : null;
	};

	const flush = async (final = false) => {
		finalizePendingHolds();
		await sendPending();
		try {
			return await chrome.runtime.sendMessage({ type: 'flush', final });
		} catch {
			return { ok: false };
		}
	};

	const stop = async (final = true, clear = false) => {
		if (enabled) detach();
		enabled = false;
		sessionId = null;
		await sendPending();
		try {
			await chrome.runtime.sendMessage({ type: 'session-stop', final, clear });
		} catch {}
	};

	const checkStatus = async () => {
		const token = localStorage.getItem('token');
		if (!token) {
			await stop(false, true);
			return;
		}
		try {
			const presence = await heartbeat();
			if (!presence?.ready) {
				if (enabled) await stop(true, false);
				return;
			}
			const response = await fetch(`${CONFIG.origin}/api/v1/experiments/telemetry/status`, {
				headers: { Accept: 'application/json', authorization: `Bearer ${token}` }
			});
			if (!response.ok) return;
			const status = await response.json();
			if (!status.enabled || status.state !== 'IN_PROGRESS' || !status.experiment_session_id) {
				if (enabled) await stop(false, false);
				return;
			}
			const result = await chrome.runtime.sendMessage({
				type: 'session-start',
				origin: CONFIG.origin,
				token,
				sessionId: status.experiment_session_id
			});
			if (!result?.ok) return;
			if (!enabled) {
				enabled = true;
				sessionId = status.experiment_session_id;
				attach();
			}
		} catch {}
	};

	window.addEventListener('open-webui-experiment-telemetry-flush', () => void flush(false));
	chrome.runtime.onMessage.addListener((message) => {
		if (message?.type !== 'telemetry-stopped') return;
		if (enabled) detach();
		enabled = false;
		sessionId = null;
		pendingEvents = [];
		pendingHolds.clear();
	});
	window.addEventListener('open-webui-experiment-telemetry-flush-request', (event) => {
		void flush(Boolean(event.detail?.final)).then((result) =>
			window.dispatchEvent(
				new CustomEvent('open-webui-experiment-telemetry-flush-result', {
					detail: { request_id: event.detail?.request_id, ok: Boolean(result?.ok) }
				})
			)
		);
	});
	window.addEventListener('open-webui-experiment-state', (event) => {
		if (event.detail?.state === 'IN_PROGRESS') void checkStatus();
		else if (enabled) void stop(false, false);
		else void heartbeat();
	});

	void checkStatus();
	statusTimer = setInterval(checkStatus, 20000);
})();
