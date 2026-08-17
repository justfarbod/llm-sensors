(() => {
	const clip = (value, limit, name, truncated) => {
		if (typeof value !== 'string') return undefined;
		if (value.length <= limit) return value;
		truncated.push(name);
		return value.slice(0, limit);
	};

	const normalizeTab = (tab) => {
		if (!tab || tab.incognito) return null;
		const truncated_fields = [];
		return {
			tab_id: tab.id ?? -1,
			window_id: tab.windowId ?? -1,
			index: tab.index ?? -1,
			...(tab.openerTabId !== undefined ? { opener_tab_id: tab.openerTabId } : {}),
			...(tab.groupId !== undefined ? { group_id: tab.groupId } : {}),
			...(tab.splitViewId !== undefined ? { split_view_id: tab.splitViewId } : {}),
			active: Boolean(tab.active),
			highlighted: Boolean(tab.highlighted),
			pinned: Boolean(tab.pinned),
			incognito: false,
			...(tab.audible !== undefined ? { audible: tab.audible } : {}),
			...(tab.autoDiscardable !== undefined ? { auto_discardable: tab.autoDiscardable } : {}),
			...(tab.discarded !== undefined ? { discarded: tab.discarded } : {}),
			...(tab.frozen !== undefined ? { frozen: tab.frozen } : {}),
			...(tab.mutedInfo
				? {
						muted_info: {
							muted: tab.mutedInfo.muted,
							...(tab.mutedInfo.reason ? { reason: tab.mutedInfo.reason } : {}),
							...(tab.mutedInfo.extensionId
								? { extension_id: tab.mutedInfo.extensionId.slice(0, 128) }
								: {})
						}
					}
				: {}),
			...(tab.status ? { status: tab.status } : {}),
			...(tab.url !== undefined ? { url: clip(tab.url, 16384, 'url', truncated_fields) } : {}),
			...(tab.pendingUrl !== undefined
				? { pending_url: clip(tab.pendingUrl, 16384, 'pending_url', truncated_fields) }
				: {}),
			...(tab.title !== undefined
				? { title: clip(tab.title, 8192, 'title', truncated_fields) }
				: {}),
			...(tab.favIconUrl !== undefined
				? { fav_icon_url: clip(tab.favIconUrl, 16384, 'fav_icon_url', truncated_fields) }
				: {}),
			...(tab.width !== undefined ? { width: tab.width } : {}),
			...(tab.height !== undefined ? { height: tab.height } : {}),
			...(tab.lastAccessed !== undefined ? { last_accessed: tab.lastAccessed } : {}),
			...(tab.sessionId !== undefined ? { session_id: tab.sessionId.slice(0, 256) } : {}),
			truncated_fields
		};
	};

	globalThis.ExperimentTabTelemetry = Object.freeze({ normalizeTab });
})();
