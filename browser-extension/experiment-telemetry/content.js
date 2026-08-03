(() => {
	if (location.pathname.startsWith('/auth') || location.pathname.startsWith('/admin')) return;

	const origin = location.origin;
	let enabled = false;
	let buffer = null;
	let statusTimer = null;
	let awayStartedAt = null;
	let lastKeydownAt = null;
	const pendingHolds = new Map();
	const removers = [];

	const id = () => crypto.randomUUID();
	const now = () => new Date().toISOString();
	const eventBase = (type, context = 'unknown') => ({
		event_id: id(),
		type,
		timestamp: now(),
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
						question_id: tagged?.getAttribute('data-question-id'),
						submission_id: tagged?.getAttribute('data-submission-id') || undefined
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
		) {
			return 'arrow/navigation';
		}
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

	const selectedMetadata = (event, field) => {
		let value = '';
		const target = event.target;
		if (
			field === 'essay' &&
			target instanceof HTMLTextAreaElement &&
			target.selectionStart !== target.selectionEnd
		) {
			value = target.value.slice(target.selectionStart, target.selectionEnd);
		} else {
			value = window.getSelection()?.toString() ?? '';
		}
		return { text_length: value.length, line_count: value ? value.split(/\r\n|\r|\n/).length : 0 };
	};

	const clipboardMetadata = (event) => {
		const value = event.clipboardData?.getData('text/plain') ?? '';
		return { text_length: value.length, line_count: value ? value.split(/\r\n|\r|\n/).length : 0 };
	};

	const markAway = () => {
		if (awayStartedAt !== null) return;
		awayStartedAt = Date.now();
		buffer.add(eventBase('focus_away'));
	};

	const markReturn = () => {
		if (awayStartedAt === null || document.hidden || !document.hasFocus()) return;
		buffer.add({ ...eventBase('focus_return'), away_duration_ms: Date.now() - awayStartedAt });
		awayStartedAt = null;
	};

	const attach = () => {
		listen(
			document,
			'keydown',
			(event) => {
				const context = fieldFor(event.target);
				if (!context || event.repeat || event.isComposing) return;
				const field = context.field;
				const classification = keyClass(event);
				const timestamp = Date.now();
				const telemetryEvent = {
					...eventBase('keystroke', context),
					key_class: classification,
					modifiers: modifiers(event)
				};
				if (lastKeydownAt !== null)
					telemetryEvent.inter_key_interval_ms = timestamp - lastKeydownAt;
				lastKeydownAt = timestamp;
				buffer.add(telemetryEvent);
				const signature = `${field}:${classification}`;
				const pending = pendingHolds.get(signature) ?? [];
				pending.push({ eventId: telemetryEvent.event_id, startedAt: timestamp });
				if (pending.length > 10) pending.shift();
				pendingHolds.set(signature, pending);
			},
			true
		);
		listen(
			document,
			'keyup',
			(event) => {
				const context = fieldFor(event.target);
				if (!context || event.isComposing) return;
				const field = context.field;
				const signature = `${field}:${keyClass(event)}`;
				const pending = pendingHolds.get(signature);
				const hold = pending?.shift();
				if (!hold) return;
				buffer.patch(hold.eventId, {
					hold_duration_ms: Math.min(Date.now() - hold.startedAt, 600000)
				});
				if (!pending.length) pendingHolds.delete(signature);
			},
			true
		);
		for (const type of ['copy', 'cut', 'paste']) {
			listen(
				document,
				type,
				(event) => {
					const context = fieldFor(event.target);
					if (!context) return;
					const field = context.field;
					const metadata =
						type === 'paste' ? clipboardMetadata(event) : selectedMetadata(event, field);
					buffer.add({ ...eventBase(type, context), ...metadata });
				},
				true
			);
		}
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
				const questionRoot = target.closest('[data-experiment-field="question"]');
				const controls = Array.from(
					questionRoot?.querySelectorAll(`[data-question-control="${controlType}"]`) ?? []
				);
				const answered = controls.some((control) =>
					control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement
						? control.type === 'checkbox' || control.type === 'radio'
							? control.checked
							: Boolean(control.value.trim())
						: false
				);
				buffer.add({ ...eventBase('answer_change', context), control_type: controlType, answered });
			},
			true
		);
		listen(document, 'visibilitychange', () => {
			buffer.add({
				...eventBase('visibility_change'),
				visibility: document.hidden ? 'hidden' : 'visible'
			});
			if (document.hidden) {
				markAway();
				void buffer.flush(true);
			} else {
				markReturn();
			}
		});
		listen(window, 'blur', () => {
			buffer.add(eventBase('window_blur'));
			pendingHolds.clear();
			markAway();
			void buffer.flush(true);
		});
		listen(window, 'focus', () => {
			buffer.add(eventBase('window_focus'));
			markReturn();
			void checkStatus();
		});
		listen(window, 'pagehide', () => void buffer.flush(true));
		listen(window, 'open-webui-experiment-telemetry-flush', () => void buffer.flush(true));
		listen(window, 'open-webui-experiment-state', (event) => {
			if (event.detail?.state !== 'IN_PROGRESS') void stop(true, true);
		});
	};

	const stop = async (flush = false, clear = false) => {
		if (!enabled) {
			if (clear) await chrome.storage.local.remove('telemetryQueue');
			return;
		}
		enabled = false;
		clearInterval(statusTimer);
		while (removers.length) removers.pop()();
		pendingHolds.clear();
		await buffer.stop({ flush });
		if (clear) await chrome.storage.local.remove('telemetryQueue');
		buffer = null;
	};

	const checkStatus = async () => {
		const token = localStorage.getItem('token');
		if (!token) {
			await stop(false, true);
			return;
		}
		try {
			const response = await fetch(`${origin}/api/v1/experiments/telemetry/status`, {
				headers: { Accept: 'application/json', authorization: `Bearer ${token}` }
			});
			if (!response.ok) {
				if ([401, 403].includes(response.status)) await stop(false, true);
				return;
			}
			const status = await response.json();
			if (!status.enabled || status.state !== 'IN_PROGRESS' || !status.experiment_session_id) {
				await stop(false, true);
				return;
			}
			if (enabled) {
				buffer.resume();
				return;
			}
			enabled = true;
			buffer = new globalThis.ExperimentTelemetryBuffer({
				origin,
				token,
				sessionId: status.experiment_session_id,
				onFatal: () => void stop(false, true)
			});
			await buffer.restore();
			attach();
			statusTimer = setInterval(checkStatus, 30000);
		} catch {}
	};

	void checkStatus();
})();
