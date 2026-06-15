class ExperimentTelemetryBuffer {
	constructor({ origin, token, sessionId, onFatal }) {
		this.origin = origin;
		this.token = token;
		this.sessionId = sessionId;
		this.onFatal = onFatal;
		this.events = [];
		this.flushing = false;
		this.offline = false;
		this.failureCycles = 0;
		this.timer = null;
		this.startTimer();
	}

	startTimer() {
		if (!this.timer) this.timer = setInterval(() => this.flush(), 5000);
	}

	async restore() {
		const { telemetryQueue } = await chrome.storage.local.get('telemetryQueue');
		if (telemetryQueue?.sessionId === this.sessionId && Array.isArray(telemetryQueue.events)) {
			this.events = telemetryQueue.events.slice(-500);
		} else {
			await chrome.storage.local.remove('telemetryQueue');
		}
	}

	add(event) {
		this.events.push(event);
		if (this.events.length > 500) this.events.splice(0, this.events.length - 500);
		if (!this.offline && this.events.length >= 50) void this.flush();
	}

	patch(eventId, values) {
		const event = this.events.find((candidate) => candidate.event_id === eventId);
		if (event) Object.assign(event, values);
	}

	async persist() {
		if (!this.events.length) {
			await chrome.storage.local.remove('telemetryQueue');
			return;
		}
		await chrome.storage.local.set({
			telemetryQueue: { sessionId: this.sessionId, events: this.events.slice(-500) }
		});
	}

	async flush(keepalive = false) {
		if (this.flushing || this.offline || !this.events.length) return;
		this.flushing = true;
		const batch = this.events.splice(0, 50);
		let response;
		try {
			for (let attempt = 0; attempt < 3; attempt += 1) {
				try {
					response = await fetch(`${this.origin}/api/v1/experiments/telemetry/events`, {
						method: 'POST',
						headers: {
							Accept: 'application/json',
							'Content-Type': 'application/json',
							authorization: `Bearer ${this.token}`
						},
						body: JSON.stringify({ experiment_session_id: this.sessionId, events: batch }),
						keepalive,
						signal: AbortSignal.timeout(10000)
					});
					if (response.ok) break;
					if ([401, 403, 409, 422].includes(response.status)) {
						this.events = [];
						await chrome.storage.local.remove('telemetryQueue');
						this.onFatal();
						return;
					}
				} catch {
					response = null;
				}
				await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
			}
			if (!response?.ok) {
				this.events = [...batch, ...this.events].slice(-500);
				this.failureCycles += 1;
				this.offline = true;
				clearInterval(this.timer);
				this.timer = null;
				await this.persist();
			} else {
				this.failureCycles = 0;
				await this.persist();
			}
		} finally {
			this.flushing = false;
			if (this.events.length >= 50) void this.flush();
		}
	}

	resume() {
		if (!this.offline || this.failureCycles >= 3) return;
		this.offline = false;
		this.startTimer();
		void this.flush();
	}

	async stop({ flush = true } = {}) {
		clearInterval(this.timer);
		if (flush) await this.flush(true);
		await this.persist();
	}
}

globalThis.ExperimentTelemetryBuffer = ExperimentTelemetryBuffer;
