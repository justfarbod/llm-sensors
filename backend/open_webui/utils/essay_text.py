import html
import re

import markdown
from bs4 import BeautifulSoup

RAW_HTML_TAG = re.compile(r'<!--[\s\S]*?-->|</?[A-Za-z][^>\n]*>')
STRIKETHROUGH = re.compile(r'~~(?=\S)(.+?)(?<=\S)~~', re.DOTALL)


def markdown_visible_text(content: str) -> str:
    """Return normalized text that is visible when an essay's Markdown is rendered."""
    escaped_source = RAW_HTML_TAG.sub(lambda match: html.escape(match.group(0)), content or '')
    escaped_source = STRIKETHROUGH.sub(r'\1', escaped_source)
    rendered = markdown.markdown(escaped_source, extensions=['extra', 'sane_lists'])
    soup = BeautifulSoup(rendered, 'html.parser')
    for image in soup.find_all('img'):
        image.replace_with(image.get('alt', ''))
    return ' '.join(soup.get_text(separator=' ', strip=True).split())


def essay_text_metrics(content: str) -> tuple[int, int]:
    visible = markdown_visible_text(content)
    return (len(visible.split()) if visible else 0, len(visible))
