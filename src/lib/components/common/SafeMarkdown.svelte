<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';
	import { escapeRawHtml } from '$lib/utils/markdownEditor';

	export let content = '';
	export let className = 'markdown-prose-sm';

	const escapeHtml = (value: string) =>
		value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
	const escapeAttribute = (value: string) => escapeHtml(value).replaceAll('"', '&quot;');
	const renderer = new marked.Renderer();
	renderer.html = (html) => escapeHtml(html);
	renderer.image = (_href, _title, text) => escapeHtml(text);
	renderer.link = (href, title, text) => {
		const safeHref = /^(https?:|mailto:|\/|#)/i.test(href) ? href : '#';
		const safeTitle = title ? ` title="${escapeAttribute(title)}"` : '';
		return `<a href="${escapeAttribute(safeHref)}"${safeTitle} target="_blank" rel="noopener noreferrer nofollow">${text}</a>`;
	};

	$: html = DOMPurify.sanitize(
		marked.parse(escapeRawHtml(content ?? ''), { breaks: true, gfm: true, renderer }) as string,
		{
			FORBID_TAGS: [
				'audio',
				'embed',
				'form',
				'iframe',
				'img',
				'input',
				'object',
				'script',
				'style',
				'video'
			],
			FORBID_ATTR: ['srcset', 'style']
		}
	);
</script>

<div class={className}>{@html html}</div>
