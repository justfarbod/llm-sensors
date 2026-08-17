import { marked, type Token, type Tokens } from 'marked';
import { decode } from 'html-entities';

export type MarkdownFormat =
	| 'bold'
	| 'italic'
	| 'heading'
	| 'bullet-list'
	| 'numbered-list'
	| 'link';

export type MarkdownEdit = {
	value: string;
	selectionStart: number;
	selectionEnd: number;
};

const RAW_HTML_TAG = /<!--[\s\S]*?-->|<\/?[A-Za-z][^>\n]*>/g;

export const escapeRawHtml = (value: string) =>
	value.replace(RAW_HTML_TAG, (tag) =>
		tag.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
	);

type TextEdit = { start: number; end: number; replacement: string };

const applyTextEdits = (
	value: string,
	selectionStart: number,
	selectionEnd: number,
	edits: TextEdit[]
): MarkdownEdit => {
	const ordered = [...edits].sort((a, b) => a.start - b.start);
	const mapPosition = (position: number) => {
		let delta = 0;
		for (const edit of ordered) {
			if (position < edit.start) break;
			if (position <= edit.end) return edit.start + delta + edit.replacement.length;
			delta += edit.replacement.length - (edit.end - edit.start);
		}
		return position + delta;
	};

	let nextValue = value;
	for (const edit of [...ordered].reverse())
		nextValue = `${nextValue.slice(0, edit.start)}${edit.replacement}${nextValue.slice(edit.end)}`;

	return {
		value: nextValue,
		selectionStart: mapPosition(selectionStart),
		selectionEnd: mapPosition(selectionEnd)
	};
};

const wrapSelection = (
	value: string,
	selectionStart: number,
	selectionEnd: number,
	opening: string,
	closing: string,
	placeholder: string
): MarkdownEdit => {
	if (
		selectionStart >= opening.length &&
		value.slice(selectionStart - opening.length, selectionStart) === opening &&
		value.slice(selectionEnd, selectionEnd + closing.length) === closing
	) {
		return {
			value: `${value.slice(0, selectionStart - opening.length)}${value.slice(
				selectionStart,
				selectionEnd
			)}${value.slice(selectionEnd + closing.length)}`,
			selectionStart: selectionStart - opening.length,
			selectionEnd: selectionEnd - opening.length
		};
	}

	const selected = value.slice(selectionStart, selectionEnd) || placeholder;
	const replacement = `${opening}${selected}${closing}`;
	return {
		value: `${value.slice(0, selectionStart)}${replacement}${value.slice(selectionEnd)}`,
		selectionStart: selectionStart + opening.length,
		selectionEnd: selectionStart + opening.length + selected.length
	};
};

const selectedLineRange = (value: string, selectionStart: number, selectionEnd: number) => {
	const start = value.lastIndexOf('\n', Math.max(0, selectionStart - 1)) + 1;
	let end = value.indexOf('\n', selectionEnd);
	if (end === -1) end = value.length;
	return { start, end };
};

const blockFormat = (
	value: string,
	selectionStart: number,
	selectionEnd: number,
	format: 'heading' | 'bullet-list' | 'numbered-list'
): MarkdownEdit => {
	const range = selectedLineRange(value, selectionStart, selectionEnd);
	const block = value.slice(range.start, range.end);
	const lines = block.split('\n');
	const nonEmpty = lines
		.map((line, index) => ({ line, index }))
		.filter(({ line }) => line.trim().length > 0);
	if (!nonEmpty.length) {
		const prefix = format === 'heading' ? '## ' : format === 'bullet-list' ? '- ' : '1. ';
		return applyTextEdits(value, selectionStart, selectionEnd, [
			{ start: range.start, end: range.start, replacement: prefix }
		]);
	}

	const unordered = /^(\s*)[-+*]\s+/;
	const ordered = /^(\s*)\d+\.\s+/;
	const heading = /^(\s*)#{1,6}\s+/;
	const allActive = nonEmpty.every(({ line }) =>
		format === 'heading'
			? /^(\s*)##\s+/.test(line)
			: format === 'bullet-list'
				? unordered.test(line)
				: ordered.test(line)
	);
	const edits: TextEdit[] = [];
	let lineOffset = range.start;
	let numberedIndex = 1;

	for (const line of lines) {
		if (line.trim()) {
			const indentLength = line.match(/^\s*/)?.[0].length ?? 0;
			const marker =
				format === 'heading' ? line.match(heading) : (line.match(unordered) ?? line.match(ordered));
			const markerStart = lineOffset + indentLength;
			const markerEnd = marker ? lineOffset + marker[0].length : markerStart;
			const replacement = allActive
				? ''
				: format === 'heading'
					? '## '
					: format === 'bullet-list'
						? '- '
						: `${numberedIndex++}. `;
			edits.push({ start: markerStart, end: markerEnd, replacement });
		}
		lineOffset += line.length + 1;
	}

	return applyTextEdits(value, selectionStart, selectionEnd, edits);
};

export const applyMarkdownFormat = (
	value: string,
	selectionStart: number,
	selectionEnd: number,
	format: MarkdownFormat
): MarkdownEdit => {
	if (format === 'bold')
		return wrapSelection(value, selectionStart, selectionEnd, '**', '**', 'bold text');
	if (format === 'italic')
		return wrapSelection(value, selectionStart, selectionEnd, '*', '*', 'italic text');
	if (format === 'link') {
		const label = value.slice(selectionStart, selectionEnd) || 'link text';
		const replacement = `[${label}](https://)`;
		return {
			value: `${value.slice(0, selectionStart)}${replacement}${value.slice(selectionEnd)}`,
			selectionStart: selectionStart + 1,
			selectionEnd: selectionStart + 1 + label.length
		};
	}
	return blockFormat(value, selectionStart, selectionEnd, format);
};

const tokenText = (token: Token): string => {
	if (token.type === 'space' || token.type === 'br' || token.type === 'hr') return ' ';
	if (token.type === 'code' || token.type === 'codespan' || token.type === 'html')
		return token.text;
	if (token.type === 'image') return decode(token.text);
	if (token.type === 'list') return token.items.map(tokenText).join(' ');
	if (token.type === 'table') {
		const table = token as Tokens.Table;
		return [
			...table.header.map((cell) => cell.tokens.map(tokenText).join('')),
			...table.rows.flatMap((row) => row.map((cell) => cell.tokens.map(tokenText).join('')))
		].join(' ');
	}
	if ('tokens' in token && Array.isArray(token.tokens)) return token.tokens.map(tokenText).join('');
	if ('text' in token && typeof token.text === 'string') return decode(token.text);
	return '';
};

export const markdownVisibleText = (value: string) =>
	marked
		.lexer(escapeRawHtml(value ?? ''))
		.map(tokenText)
		.join(' ')
		.replace(/\s+/g, ' ')
		.trim();

export const markdownTextMetrics = (value: string) => {
	const text = markdownVisibleText(value);
	return {
		text,
		wordCount: text ? text.split(/\s+/u).length : 0,
		characterCount: text.length
	};
};
