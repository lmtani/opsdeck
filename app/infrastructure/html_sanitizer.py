"""Cleans the HTML GitHub renders from Markdown before it reaches the page.

GitHub already sanitizes body_html; this is a second, independent allowlist
(nh3, the Rust ammonia sanitizer) so the dashboard never trusts a remote
service with what it injects into its own DOM.
"""

import nh3

_TAGS = nh3.ALLOWED_TAGS | {"input"}  # the checkboxes of task lists
_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "input": {"checked"},  # type is limited to checkbox below; disabled is always set
    "ol": {"start"},
    "td": {"align"},
    "th": {"align"},
}


def sanitize_html(html: str) -> str:
    return nh3.clean(
        html or "",
        tags=_TAGS,
        attributes=_ATTRIBUTES,
        tag_attribute_values={"input": {"type": {"checkbox"}}},
        set_tag_attribute_values={
            "a": {"target": "_blank"},
            "img": {"loading": "lazy"},
            "input": {"disabled": "disabled"},
        },
        url_schemes={"https", "http", "mailto"},
        link_rel="noopener noreferrer",
        url_relative=("rewrite_with_base", "https://github.com/"),
    )
