import { render } from 'svelte/server';
import { readable } from 'svelte/store';
import { describe, expect, it } from 'vitest';

import MarkdownEditor from './MarkdownEditor.svelte';

const context = new Map([
	[
		'i18n',
		readable({
			t: (key: string) => key
		})
	]
]);

describe('MarkdownEditor', () => {
	it('renders the participant textarea and visible formatting bar', () => {
		const { body } = render(MarkdownEditor, {
			props: {
				value: 'Essay',
				ariaLabel: 'Essay',
				experimentField: 'essay',
				sessionTaskId: 'task-1'
			},
			context
		});

		expect(body).toContain('data-experiment-field="essay"');
		expect(body).toContain('data-session-task-id="task-1"');
		expect(body).toContain('aria-label="Markdown formatting"');
		expect(body).toContain('aria-label="Bold"');
		expect(body).toContain('>Markdown</span>');
		expect(body).toContain('>Preview</button>');
	});
});
