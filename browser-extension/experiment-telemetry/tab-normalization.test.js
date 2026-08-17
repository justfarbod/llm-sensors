import { describe, expect, it } from 'vitest';
import './tab-normalization.js';

const { normalizeTab } = globalThis.ExperimentTabTelemetry;

describe('experiment tab normalization', () => {
	it('preserves complete normal-tab metadata and Chrome field names', () => {
		const tab = normalizeTab({
			id: 8,
			windowId: 2,
			index: 1,
			openerTabId: 4,
			groupId: -1,
			splitViewId: 7,
			active: true,
			highlighted: true,
			pinned: false,
			incognito: false,
			audible: true,
			autoDiscardable: false,
			discarded: false,
			frozen: false,
			mutedInfo: { muted: true, reason: 'user' },
			status: 'complete',
			url: 'https://example.com/path?token=complete#fragment',
			pendingUrl: 'https://example.com/next?full=yes',
			title: 'Exact title',
			favIconUrl: 'https://example.com/icon.png',
			width: 1200,
			height: 800,
			lastAccessed: 123456,
			sessionId: 'restored-session'
		});

		expect(tab.url).toBe('https://example.com/path?token=complete#fragment');
		expect(tab.pending_url).toBe('https://example.com/next?full=yes');
		expect(tab.title).toBe('Exact title');
		expect(tab.fav_icon_url).toBe('https://example.com/icon.png');
		expect(tab).toMatchObject({
			tab_id: 8,
			window_id: 2,
			opener_tab_id: 4,
			split_view_id: 7,
			last_accessed: 123456,
			incognito: false
		});
	});

	it('excludes incognito tabs and marks bounded-field truncation', () => {
		expect(normalizeTab({ id: 1, incognito: true })).toBeNull();
		const normalized = normalizeTab({
			id: 1,
			windowId: 1,
			index: 0,
			incognito: false,
			title: 'x'.repeat(8200)
		});
		expect(normalized.title).toHaveLength(8192);
		expect(normalized.truncated_fields).toEqual(['title']);
	});
});
