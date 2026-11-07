"""Markdown 渲染与 HTML 净化（AC-13；FRONTEND-DESIGN §7）。

- 原始 HTML 不作为 HTML 解析（`html=False`），`<script>` 会被转义为文本；
- 再经 nh3 清洗，禁止事件属性与 `javascript:` URL；
- 图片走同源代理路径，外链图片不直接加载。
"""

from __future__ import annotations

import re

import nh3
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
_md.enable(["table", "strikethrough"])

_ALLOWED_TAGS = {
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "del",
    "blockquote",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "code",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "a",
    "img",
    "span",
    "div",
}
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height", "loading"},
    "code": {"class"},
    "th": {"align"},
    "td": {"align"},
    "span": {"class"},
    "div": {"class"},
}
_EXTERNAL_IMG = re.compile(r'src="(https?://[^"]+)"', re.IGNORECASE)


def _rewrite_images(html: str, document_id: str) -> str:
    """外链图片不直接加载：改为服务端代理（外链只作为路径参数，不发起请求）。"""

    def replace(match: re.Match[str]) -> str:
        return f'src="/api/v1/documents/{document_id}/asset?path={match.group(1)}"'

    return _EXTERNAL_IMG.sub(replace, html)


def render_markdown(markdown_text: str, *, document_id: str) -> str:
    raw = _md.render(markdown_text or "")
    raw = _rewrite_images(raw, document_id)
    return nh3.clean(
        raw,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )
