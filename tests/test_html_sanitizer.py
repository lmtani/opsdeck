"""The second allowlist over GitHub's rendered Markdown."""

from app.infrastructure.github.html_sanitizer import sanitize_html


def test_scripts_handlers_and_javascript_urls_are_dropped():
    html = sanitize_html(
        '<p>hi<script>alert(1)</script></p><img src="https://x/i.png" onerror="alert(1)">'
        '<a href="javascript:alert(1)">x</a><iframe src="https://evil"></iframe>'
    )
    assert "script" not in html and "onerror" not in html
    assert "javascript:" not in html and "iframe" not in html
    assert '<img src="https://x/i.png" loading="lazy">' in html


def test_links_open_outside_and_relative_ones_point_at_github():
    html = sanitize_html('<a href="/acme/core/issues/1" target="_self">#1</a>')
    assert 'href="https://github.com/acme/core/issues/1"' in html
    assert 'target="_blank"' in html and 'rel="noopener noreferrer"' in html


def test_task_list_checkboxes_survive_but_cannot_be_used():
    html = sanitize_html('<li><input type="checkbox" checked> done</li><input type="password">')
    assert '<input type="checkbox" checked="" disabled="disabled">' in html
    assert "password" not in html
