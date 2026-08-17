import { describe, expect, it } from 'vitest';

import { applyMarkdownFormat, markdownTextMetrics, markdownVisibleText } from './markdownEditor';

describe('Markdown editor formatting', () => {
	it('wraps and unwraps selected inline text', () => {
		const bold = applyMarkdownFormat('hello world', 6, 11, 'bold');
		expect(bold).toEqual({ value: 'hello **world**', selectionStart: 8, selectionEnd: 13 });
		expect(applyMarkdownFormat(bold.value, bold.selectionStart, bold.selectionEnd, 'bold')).toEqual(
			{
				value: 'hello world',
				selectionStart: 6,
				selectionEnd: 11
			}
		);
	});

	it('inserts an editable placeholder without a selection', () => {
		expect(applyMarkdownFormat('', 0, 0, 'italic')).toEqual({
			value: '*italic text*',
			selectionStart: 1,
			selectionEnd: 12
		});
	});

	it('adds and removes multiline list markers', () => {
		const listed = applyMarkdownFormat('one\ntwo', 0, 7, 'numbered-list');
		expect(listed.value).toBe('1. one\n2. two');
		expect(applyMarkdownFormat(listed.value, 0, listed.value.length, 'numbered-list').value).toBe(
			'one\ntwo'
		);
	});

	it('normalizes an existing block marker when changing format', () => {
		expect(applyMarkdownFormat('- item', 0, 6, 'heading').value).toBe('## - item');
		expect(applyMarkdownFormat('# title', 0, 7, 'heading').value).toBe('## title');
	});

	it('creates links and keeps the label selected', () => {
		expect(applyMarkdownFormat('OpenAI', 0, 6, 'link')).toEqual({
			value: '[OpenAI](https://)',
			selectionStart: 1,
			selectionEnd: 7
		});
	});
});

describe('Markdown visible-text metrics', () => {
	it('counts rendered prose instead of structural Markdown', () => {
		const source = '## Heading\n\n- **One**\n- [Two](https://example.com)';
		expect(markdownVisibleText(source)).toBe('Heading One Two');
		expect(markdownTextMetrics(source)).toEqual({
			text: 'Heading One Two',
			wordCount: 3,
			characterCount: 15
		});
	});

	it('keeps malformed Markdown and escaped raw HTML visible', () => {
		expect(markdownVisibleText('An **open marker')).toBe('An **open marker');
		expect(markdownVisibleText('<b>literal</b>')).toBe('<b>literal</b>');
		expect(markdownVisibleText('<!-- note --> and ~~old~~ plus `code`')).toBe(
			'<!-- note --> and old plus code'
		);
	});
});
