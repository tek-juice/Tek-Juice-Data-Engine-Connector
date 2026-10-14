"""Make published content discoverable by search engines.

Two capabilities, advertised to the Data Engine when the content store
supports them:

``seo.prerender``
    Every published article URL can be served as complete HTML: the page's
    own title, meta description, canonical URL, Open Graph / Twitter tags,
    ``Article`` JSON-LD and the article text, inside the site's normal page
    shell. Crawlers that do not run JavaScript see the article; for visitors
    the site's app replaces the pre-rendered markup when it starts (the
    article goes into the app's root element).

``seo.sitemap``
    ``sitemap.xml`` listing the site's public pages and every published
    article with its last modification date.

Both work from the content store (``get_content`` and ``list_published``)
and never require the site's frontend to change. The site's web server
routes article URLs and ``/sitemap.xml`` to :class:`SeoPages` (see the
framework adapters and docs/publication-seo.md).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from tekjuice_connector.publication.contract import PublicationContent

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)|(https?://[^\s<>()]+[^\s<>().,;:!?])")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITEM = re.compile(r"^\s*(?:[*+-]|(\d+)\.)\s+(.*)$")


@dataclass(frozen=True)
class PublishedPage:
    """One published article, for the sitemap."""

    slug: str
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Markdown -> HTML (the subset the Data Engine writes), always escaped
# ---------------------------------------------------------------------------


def _inline(text: str) -> str:
    out: List[str] = []
    last = 0
    for match in _LINK.finditer(text):
        out.append(_BOLD.sub(r"<strong>\1</strong>", html.escape(text[last:match.start()])))
        if match.group(1):
            label, href = match.group(1), match.group(2)
        else:
            label = href = match.group(3)
        out.append(f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>')
        last = match.end()
    out.append(_BOLD.sub(r"<strong>\1</strong>", html.escape(text[last:])))
    return "".join(out)


def markdown_to_html(markdown: str) -> str:
    """Headings, paragraphs, lists, links and bold; all text is escaped and
    only http(s) addresses become links."""
    blocks = re.split(r"\n\s*\n", (markdown or "").replace("\r\n", "\n").strip())
    parts: List[str] = []
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", lines[0])
        if heading and len(lines) == 1:
            level = min(len(heading.group(1)) + 1, 6)  # the page title is the h1
            parts.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            continue
        items = [_ITEM.match(line) for line in lines]
        if all(items):
            tag = "ol" if items[0].group(1) else "ul"
            inner = "".join(f"<li>{_inline(m.group(2).strip())}</li>" for m in items)
            parts.append(f"<{tag}>{inner}</{tag}>")
            continue
        parts.append(f"<p>{_inline(' '.join(line.strip() for line in lines))}</p>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

_PAGE_SPECIFIC_HEAD = re.compile(
    r"<meta\s+[^>]*(?:name|property)=[\"'](?:description|keywords|og:[^\"']+|twitter:[^\"']+)[\"'][^>]*>\s*"
    r"|<link\s+[^>]*rel=[\"']canonical[\"'][^>]*>\s*",
    re.I,
)


def _description(content: PublicationContent) -> str:
    text = content.meta_description or content.excerpt or ""
    if not text:
        first = re.sub(r"[#*\[\]()]", "", (content.body or "").strip().split("\n\n")[0])
        text = first.strip()
    return text[:300]


def render_page(
    shell_html: str,
    *,
    content: PublicationContent,
    canonical_url: str,
    site_name: Optional[str] = None,
    modified_time: Optional[datetime] = None,
) -> str:
    """Return *shell_html* with the article's own head tags and its text in
    the app's root element (or right after ``<body>`` if there is none)."""
    title = html.escape(content.title)
    description = html.escape(_description(content), quote=True)
    url = html.escape(canonical_url, quote=True)
    page = _PAGE_SPECIFIC_HEAD.sub("", shell_html)

    if re.search(r"<title>.*?</title>", page, re.I | re.S):
        page = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", page, count=1, flags=re.I | re.S)
    json_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": content.title,
        "description": _description(content),
        "url": canonical_url,
        "mainEntityOfPage": canonical_url,
    }
    if modified_time:
        json_ld["dateModified"] = modified_time.isoformat()
    if site_name:
        json_ld["publisher"] = {"@type": "Organization", "name": site_name}
    head = "\n".join([
        f'<meta name="description" content="{description}" />',
        f'<link rel="canonical" href="{url}" />',
        '<meta property="og:type" content="article" />',
        f'<meta property="og:title" content="{title}" />',
        f'<meta property="og:description" content="{description}" />',
        f'<meta property="og:url" content="{url}" />',
        *([f'<meta property="og:site_name" content="{html.escape(site_name, quote=True)}" />'] if site_name else []),
        '<meta name="twitter:card" content="summary" />',
        f'<meta name="twitter:title" content="{title}" />',
        f'<meta name="twitter:description" content="{description}" />',
        '<script type="application/ld+json">'
        + json.dumps(json_ld, ensure_ascii=False).replace("</", "<\\/")
        + "</script>",
    ])
    if "</head>" in page:
        page = page.replace("</head>", head + "\n</head>", 1)
    else:
        page = head + page

    article = f"<article><h1>{title}</h1>\n{markdown_to_html(content.body)}</article>"
    root = re.search(r'(<div\s+id=["\']root["\'][^>]*>)(\s*)(</div>)', page, re.I)
    if root:
        page = page[:root.end(1)] + article + page[root.start(3):]
    elif re.search(r"<body[^>]*>", page, re.I):
        page = re.sub(r"(<body[^>]*>)", r"\1" + article.replace("\\", "\\\\"), page, count=1, flags=re.I)
    else:
        page += article
    return page


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------


def build_sitemap(entries: Iterable[Tuple[str, Optional[datetime]]]) -> str:
    """``sitemap.xml`` for absolute URLs with optional last-modified times."""
    seen = set()
    rows = []
    for url, modified in entries:
        if url in seen:
            continue
        seen.add(url)
        lastmod = f"<lastmod>{modified.date().isoformat()}</lastmod>" if modified else ""
        rows.append(f"  <url><loc>{html.escape(url)}</loc>{lastmod}</url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows)
        + "\n</urlset>\n"
    )


class SeoPages:
    """Pre-rendered article pages and a sitemap for a content store.

    ``shell_html`` returns the site's normal HTML page (its ``index.html``),
    so styles and the app still load for visitors. ``static_paths`` are the
    site's own public pages to list in the sitemap ("/", "/features", ...).
    """

    def __init__(
        self,
        store,
        *,
        site_url: str,
        shell_html: Callable[[], str],
        static_paths: Sequence[str] = ("/",),
        site_name: Optional[str] = None,
    ) -> None:
        if not callable(getattr(store, "get_content", None)):
            raise TypeError("The content store must implement get_content(slug) to pre-render pages")
        self._store = store
        self._site = site_url.rstrip("/")
        self._shell = shell_html
        self._static = tuple(static_paths)
        self._site_name = site_name

    def page(self, slug: str) -> Optional[str]:
        """Complete HTML for a published article, or ``None`` if *slug* is not one."""
        content = self._store.get_content(slug)
        if content is None:
            return None
        modified = None
        lister = getattr(self._store, "list_published", None)
        if callable(lister):
            modified = next((p.updated_at for p in lister() if p.slug == slug), None)
        return render_page(
            self._shell(),
            content=content,
            canonical_url=self._store.public_url(slug),
            site_name=self._site_name,
            modified_time=modified,
        )

    def sitemap(self) -> str:
        entries: List[Tuple[str, Optional[datetime]]] = [
            (self._site + (path if path.startswith("/") else "/" + path), None)
            for path in self._static
        ]
        lister = getattr(self._store, "list_published", None)
        if callable(lister):
            entries += [(self._store.public_url(p.slug), p.updated_at) for p in lister()]
        return build_sitemap(entries)
