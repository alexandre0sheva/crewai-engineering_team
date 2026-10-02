"""Readable text from fetched pages: HTML to Markdown with boilerplate removed.

Standard library only. The page is parsed into a small tree; the main content is the first
``<main>``, else the first substantial ``<article>``, else the body. Scripts, styles, comments,
navigation, footers, sidebars, forms, cookie banners, and *hidden* elements (``hidden``,
``aria-hidden``, ``display:none``) are dropped, so text a person could not see never reaches the
agent. Links are resolved against the page URL and reduced to their text unless they are http(s).
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
SKIP = frozenset(
    {"script", "style", "noscript", "template", "svg", "iframe", "canvas", "head", "object"}
    | {"nav", "footer", "aside", "form", "select", "button", "dialog", "menu"}
)
BOILERPLATE = re.compile(
    r"(?:^|[\s_-])(?:nav|navbar|menu|sidebar|footer|cookie|cookies|banner|advert|ads?|promo"
    r"|share|social|breadcrumbs?|pagination|newsletter|popup|modal|skip-link)(?:$|[\s_-])",
    re.IGNORECASE,
)
HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
BLOCKS = frozenset(
    {"p", "div", "section", "article", "main", "header", "li", "ul", "ol", "table", "tr"}
    | {"h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "hr", "figure", "dl", "dt", "dd"}
)
MAX_NODES = 60_000
MAX_DEPTH = 120
MIN_ARTICLE_CHARS = 200


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)
    parent: Node | None = field(default=None, repr=False)

    def text_length(self) -> int:
        return sum(len(c.strip()) if isinstance(c, str) else c.text_length() for c in self.children)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self._current = self.root
        self._depth = 0
        self._count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._count += 1
        if self._count > MAX_NODES:
            return
        node = Node(tag, {k: (v or "") for k, v in attrs}, parent=self._current)
        self._current.children.append(node)
        if tag not in VOID and self._depth < MAX_DEPTH:
            self._current = node
            self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self._current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self._current = node.parent
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._count <= MAX_NODES:
            self._current.children.append(data)


def _hidden(node: Node) -> bool:
    attrs = node.attrs
    return (
        "hidden" in attrs
        or attrs.get("aria-hidden", "").lower() == "true"
        or bool(HIDDEN_STYLE.search(attrs.get("style", "")))
    )


def _dropped(node: Node) -> bool:
    if node.tag in SKIP or _hidden(node):
        return True
    label = f"{node.attrs.get('class', '')} {node.attrs.get('id', '')}"
    role = node.attrs.get("role", "").lower()
    if role in ("navigation", "banner", "contentinfo", "complementary", "search"):
        return True
    return bool(label.strip()) and bool(BOILERPLATE.search(label))


def _find(node: Node, tag: str) -> list[Node]:
    found: list[Node] = []
    for child in node.children:
        if isinstance(child, Node):
            if child.tag == tag:
                found.append(child)
            found.extend(_find(child, tag))
    return found


def _content_root(root: Node) -> Node:
    for main in _find(root, "main"):
        return main
    for article in _find(root, "article"):
        if article.text_length() >= MIN_ARTICLE_CHARS:
            return article
    bodies = _find(root, "body")
    return bodies[0] if bodies else root


class _Renderer:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.out: list[str] = []

    # -- helpers -----------------------------------------------------------------------

    def _emit(self, text: str) -> None:
        self.out.append(text)

    def _break(self, count: int = 2) -> None:
        text = "".join(self.out)
        have = len(text) - len(text.rstrip("\n"))
        if text and have < count:
            self.out.append("\n" * (count - have))

    def _href(self, href: str) -> str | None:
        href = href.strip()
        if not href or href.startswith("#"):
            return None
        resolved = urljoin(self.base_url, href) if self.base_url else href
        return resolved if resolved.lower().startswith(("http://", "https://")) else None

    def _inline(self, node: Node) -> str:
        sub = _Renderer(self.base_url)
        sub.children(node)
        return re.sub(r"\s+", " ", "".join(sub.out)).strip()

    # -- the walk ----------------------------------------------------------------------

    def children(self, node: Node, *, pre: bool = False, indent: int = 0) -> None:
        for child in node.children:
            if isinstance(child, str):
                text = child if pre else re.sub(r"\s+", " ", child)
                if text.strip() or pre:
                    self._emit(text)
            else:
                self.node(child, pre=pre, indent=indent)

    def node(self, node: Node, *, pre: bool, indent: int) -> None:
        tag = node.tag
        if _dropped(node):
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = self._inline(node)
            if text:
                self._break()
                self._emit(f"{'#' * int(tag[1])} {text}")
                self._break()
        elif tag == "pre":
            self._break()
            language = _language(node)
            code = _plain(node).strip("\n")
            self._emit(f"```{language}\n{code}\n```")
            self._break()
        elif tag in ("ul", "ol"):
            self._list(node, ordered=tag == "ol", indent=indent)
        elif tag == "table":
            self._table(node)
        elif tag == "blockquote":
            self._break()
            quoted = _Renderer(self.base_url)
            quoted.children(node)
            body = re.sub(r"\n{3,}", "\n\n", "".join(quoted.out)).strip()
            self._emit("\n".join(f"> {line}" if line else ">" for line in body.splitlines()))
            self._break()
        elif tag == "a":
            text = self._inline(node)
            link = self._href(node.attrs.get("href", ""))
            if text:
                self._emit(f"[{text}]({link})" if link else text)
        elif tag == "code":
            text = self._inline(node)
            if text:
                self._emit(f"`{text}`")
        elif tag in ("strong", "b"):
            text = self._inline(node)
            if text:
                self._emit(f"**{text}**")
        elif tag in ("em", "i"):
            text = self._inline(node)
            if text:
                self._emit(f"*{text}*")
        elif tag == "br":
            self._emit("\n")
        elif tag == "hr":
            self._break()
            self._emit("---")
            self._break()
        elif tag == "img":
            alt = node.attrs.get("alt", "").strip()
            if alt:
                self._emit(f"[image: {alt}]")
        else:
            block = tag in BLOCKS
            if block:
                self._break()
            self.children(node, pre=pre, indent=indent)
            if block:
                self._break()

    def _list(self, node: Node, *, ordered: bool, indent: int) -> None:
        self._break(1 if indent else 2)
        number = 0
        for item in node.children:
            if not isinstance(item, Node) or item.tag != "li" or _dropped(item):
                continue
            number += 1
            marker = f"{number}." if ordered else "-"
            lead = " " * indent + marker + " "
            sub = _Renderer(self.base_url)
            nested: list[Node] = []
            for part in item.children:
                if isinstance(part, Node) and part.tag in ("ul", "ol"):
                    nested.append(part)
                else:
                    sub.children(Node("#li", children=[part]))
            self._emit(lead + re.sub(r"\s+", " ", "".join(sub.out)).strip() + "\n")
            for part in nested:
                inner = _Renderer(self.base_url)
                inner._list(part, ordered=part.tag == "ol", indent=indent + 3)
                self._emit("".join(inner.out))
        self._break(1 if indent else 2)

    def _table(self, node: Node) -> None:
        rows = [
            [self._inline(cell) for cell in _find(row, "th") + _find(row, "td")]
            for row in _find(node, "tr")
        ]
        rows = [row for row in rows if any(row)]
        if not rows:
            return
        width = max(len(row) for row in rows)
        padded = [row + [""] * (width - len(row)) for row in rows]
        self._break()
        self._emit("| " + " | ".join(padded[0]) + " |\n")
        self._emit("|" + " --- |" * width + "\n")
        for row in padded[1:]:
            self._emit("| " + " | ".join(row) + " |\n")
        self._break()


def _plain(node: Node) -> str:
    return "".join(c if isinstance(c, str) else _plain(c) for c in node.children)


def _language(node: Node) -> str:
    for candidate in [node, *[c for c in node.children if isinstance(c, Node)]]:
        match = re.search(r"(?:language|lang)-([\w+#-]+)", candidate.attrs.get("class", ""))
        if match:
            return match.group(1)
    return ""


def _title(root: Node) -> str:
    for title in _find(root, "title"):
        return re.sub(r"\s+", " ", _plain(title)).strip()
    return ""


def _cap(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return f"{text[:max_chars].rstrip()}\n\n... page truncated at {max_chars} characters."
    return text


def html_to_markdown(html: str, *, base_url: str = "", max_chars: int = 20_000) -> str:
    """The readable content of ``html`` as Markdown, headed by the page title."""

    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    renderer = _Renderer(base_url)
    renderer.children(_content_root(builder.root))
    body = re.sub(r"[ \t]+\n", "\n", "".join(renderer.out))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    title = _title(builder.root)
    text = f"# {title}\n\n{body}" if title else body
    return _cap(text, max_chars)


def to_text(content_type: str, data: bytes, *, base_url: str = "", max_chars: int = 20_000) -> str:
    """A response body as text: HTML to Markdown, JSON pretty-printed, other text as is."""

    kind = content_type.split(";")[0].strip().lower()
    charset = "utf-8"
    if "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
    try:
        text = data.decode(charset, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    if kind in ("text/html", "application/xhtml+xml"):
        return html_to_markdown(text, base_url=base_url, max_chars=max_chars)
    if "json" in kind:
        with contextlib.suppress(ValueError):
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    return _cap(text, max_chars)
