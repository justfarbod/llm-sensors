import { afterEach, describe, expect, it, vi } from 'vitest';
import { deterministicPreviewChunks, waitUntilPreviewDeadline } from './experimentTimingPreview';

afterEach(() => vi.useRealTimers());

describe('experiment timing preview', () => {
	it('waits for the literal configured deadline', async () => {
		vi.useFakeTimers();
		vi.setSystemTime(0);
		const startedAt = Date.now();
		let completed = false;
		const waiting = waitUntilPreviewDeadline(
			startedAt + 5000,
			() => true,
			() => Date.now()
		).then((value) => (completed = value));

		await vi.advanceTimersByTimeAsync(4999);
		expect(completed).toBe(false);
		await vi.advanceTimersByTimeAsync(1);
		await waiting;
		expect(completed).toBe(true);
	});

	it('stops a stale preview without waiting for its deadline', async () => {
		vi.useFakeTimers();
		let active = true;
		const waiting = waitUntilPreviewDeadline(performance.now() + 5000, () => active);
		active = false;
		await vi.advanceTimersByTimeAsync(50);
		await expect(waiting).resolves.toBe(false);
	});

	it('preserves text while keeping non-final chunks inside the configured range', () => {
		const chunks = deterministicPreviewChunks(
			'This is a representative preview response.',
			'CHARACTER',
			3,
			7,
			42
		);

		expect(chunks.join('')).toBe('This is a representative preview response.');
		expect(chunks.slice(0, -1).every((chunk) => chunk.length >= 3 && chunk.length <= 7)).toBe(true);
		expect(chunks.at(-1)!.length).toBeLessThanOrEqual(7);
	});
});
