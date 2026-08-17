import pytest

from open_webui.utils.essay_text import essay_text_metrics, markdown_visible_text


@pytest.mark.parametrize(
    ('source', 'visible'),
    [
        ('Plain text', 'Plain text'),
        ('## Heading\n\n- **One**\n- [Two](https://example.com)', 'Heading One Two'),
        ('An **open marker', 'An **open marker'),
        ('<b>literal</b>', '<b>literal</b>'),
        ('<!-- note --> and ~~old~~ plus `code`', '<!-- note --> and old plus code'),
        ('Lots\n\n\tof   whitespace', 'Lots of whitespace'),
    ],
)
def test_markdown_visible_text(source, visible):
    assert markdown_visible_text(source) == visible


def test_essay_metrics_count_normalized_visible_text():
    assert essay_text_metrics('## Heading\n\n- **One**\n- [Two](https://example.com)') == (3, 15)
