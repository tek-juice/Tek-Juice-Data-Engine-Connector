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

``ai.markdown`` / ``ai.llms_txt`` / ``ai.feed`` / ``ai.robots`` (LEO)
    Machine-readable versions for AI systems: a Markdown copy of every
    article, ``llms.txt`` / ``llms-full.txt``, a JSON Feed and a robots.txt
    that welcomes AI crawlers. Every output names the article's canonical
    URL on the customer's own domain, so AI answers credit and link it.

GEO/AEO/voice material sent by the Data Engine (``content.discovery``:
summary, key takeaways, entities, direct answer, FAQ, citations) is rendered
as Article / FAQPage / BreadcrumbList JSON-LD with ``speakable`` markup and
as visible text, always rewritten to the canonical URL.

All work from the content store (``get_content`` and ``list_published``)
and never require the site's frontend to change. The site's web server
routes article URLs and ``/sitemap.xml`` to :class:`SeoPages` (see the
framework adapters and docs/publication-seo.md).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
from urllib.parse import urlsplit
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Callable, Iterable, List, Optional, Sequence, Tuple

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


_SOFTWARE_WORDS = ("software", "app", "application", "saas", "platform", "web based", "web-based", "online tool")
_FINANCE_WORDS = ("finance", "financial", "budget", "expense", "accounting", "money", "invoice", "payroll", "banking")
_CURRENCIES = {"£": "GBP", "$": "USD", "€": "EUR"}
_PRICE = re.compile(r"([£$€])\s?(\d[\d,]*(?:\.\d{1,2})?)")


def _text_of(entity: dict) -> str:
    parts = [entity.get(k) or "" for k in ("product_type", "category", "description")]
    parts += [*(entity.get("features") or []), *(entity.get("use_cases") or [])]
    return " ".join(str(p) for p in parts).lower()


def offers_from_pricing(pricing: Sequence[str]) -> Optional[dict]:
    """An Offer from verified pricing, only when the price and currency are
    stated unambiguously ("£0", "$9.99 per month"); several prices give an
    AggregateOffer. Free without a stated currency gives nothing."""
    found = [(_CURRENCIES[sym], amount.replace(",", "")) for text in pricing for sym, amount in _PRICE.findall(text)]
    currencies = {c for c, _ in found}
    if not found or len(currencies) != 1:
        return None
    currency = currencies.pop()
    prices = sorted({float(a) for _, a in found})
    fmt = lambda v: f"{v:.2f}".rstrip("0").rstrip(".")  # noqa: E731
    if len(prices) == 1:
        return {"@type": "Offer", "price": fmt(prices[0]), "priceCurrency": currency}
    return {"@type": "AggregateOffer", "lowPrice": fmt(prices[0]), "highPrice": fmt(prices[-1]),
            "priceCurrency": currency, "offerCount": len(prices)}


def _shows_price(page_text: str, offer: dict) -> bool:
    """The page states the offer's price with its currency ("£0", "$9.99"),
    or says "free" when the price is zero."""
    symbol = next(sym for sym, code in _CURRENCIES.items() if code == offer["priceCurrency"])
    amounts = [offer.get("price"), offer.get("lowPrice")]
    if any(a and re.search(re.escape(symbol) + r"\s?" + re.escape(a) + r"(?![\d.])", page_text) for a in amounts):
        return True
    return "0" in amounts and re.search(r"\bfree\b", page_text) is not None


def entity_json_ld(entity: dict, *, site_url: str, visible_text: Optional[str] = None) -> List[dict]:
    """Organization and product nodes from the business's verified facts.

    The organization's URL is always *site_url* (the site serving them).
    Software products are SoftwareApplication nodes; the Offer appears only
    when the verified pricing states a price and currency. With
    *visible_text* (an article page), a product node is included only when
    the page shows the product's name, and its offer only when the page
    shows the price, so structured data never describes what the page does
    not show.
    """
    if not entity.get("name"):
        return []
    base = site_url.rstrip("/")
    org_id = base + "/#organization"
    org = {"@context": "https://schema.org", "@type": "Organization", "@id": org_id,
           "name": entity["name"], "url": site_url}
    if entity.get("description"):
        org["description"] = entity["description"]
    if entity.get("locations"):
        org["areaServed"] = entity["locations"]
    nodes = [org]
    facts_text = _text_of(entity)
    software = any(w in facts_text for w in _SOFTWARE_WORDS)
    offer = offers_from_pricing(entity.get("pricing") or [])
    page_text = (visible_text or "").lower()
    for name in entity.get("products") or []:
        if visible_text is not None and not re.search(r"(?<![\w])" + re.escape(name.lower()) + r"(?![\w])", page_text):
            continue
        node = {"@context": "https://schema.org", "@type": "SoftwareApplication" if software else "Product",
                "@id": f"{base}/#product-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}",
                "name": name, "url": site_url}
        node["publisher" if software else "brand"] = {"@id": org_id}
        if software:
            if any(w in facts_text for w in _FINANCE_WORDS):
                node["applicationCategory"] = "FinanceApplication"
            elif entity.get("category"):
                node["applicationCategory"] = entity["category"]
            if "browser" in facts_text or "web based" in facts_text or "web-based" in facts_text:
                node["operatingSystem"] = "Web browser"
        elif entity.get("product_type") or entity.get("category"):
            node["category"] = entity.get("product_type") or entity.get("category")
        if entity.get("description"):
            node["description"] = entity["description"]
        if entity.get("features"):
            if software:
                node["featureList"] = list(entity["features"])[:20]
            else:
                node["additionalProperty"] = [{"@type": "PropertyValue", "name": "feature", "value": v}
                                              for v in entity["features"][:20]]
        if entity.get("audiences"):
            node["audience"] = [{"@type": "Audience", "audienceType": a} for a in entity["audiences"][:10]]
        shows_price = visible_text is None or (offer is not None and _shows_price(page_text, offer))
        if offer and shows_price:
            node["offers"] = {**offer, "url": site_url}
        nodes.append(node)
    return nodes


def _structured_data(
    content: PublicationContent,
    *,
    canonical_url: str,
    site_url: Optional[str],
    site_name: Optional[str],
    modified_time: Optional[datetime],
    entity: Optional[dict] = None,
) -> List[dict]:
    """JSON-LD for the article. URLs always point at *canonical_url*; the
    Data Engine's json_ld supplies extra Article properties only."""
    discovery = content.discovery or {}
    supplied = next(
        (dict(item) for item in discovery.get("json_ld") or []
         if item.get("@type") in ("Article", "BlogPosting", "NewsArticle")),
        {},
    )
    article = {
        **{k: v for k, v in supplied.items() if k not in ("url", "mainEntityOfPage", "@id")},
        "@context": "https://schema.org",
        "@type": supplied.get("@type", "Article"),
        "@id": f"{canonical_url}#article",
        "headline": content.title[:110],
        "description": _description(content),
        "url": canonical_url,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical_url},
        "isAccessibleForFree": True,
        # Voice assistants read the direct answer and the summary.
        "speakable": {"@type": "SpeakableSpecification", "cssSelector": ["#tj-answer", "#tj-summary"]},
    }
    if discovery.get("language"):
        article["inLanguage"] = discovery["language"]
    if modified_time:
        article["dateModified"] = modified_time.isoformat()
    visible = " ".join([content.title, content.body or "",
                        *(str(v) for k, v in discovery.items() if k in ("direct_answer", "summary")),
                        *(discovery.get("key_takeaways") or [])])
    entity_nodes = (entity_json_ld(entity, site_url=site_url, visible_text=visible)
                    if entity and site_url else [])
    if entity_nodes:
        article["publisher"] = {"@id": entity_nodes[0]["@id"]}
        article.setdefault("author", {"@id": entity_nodes[0]["@id"]})
    elif site_name:
        org = {"@type": "Organization", "name": site_name}
        if site_url:
            org["url"] = site_url
        article["publisher"] = org
        article.setdefault("author", org)
    if discovery.get("entities"):
        article["about"] = [{"@type": "Thing", "name": name} for name in discovery["entities"][:12]]
    if len(entity_nodes) > 1:
        article["mentions"] = [{"@id": node["@id"]} for node in entity_nodes[1:]]
    if discovery.get("citations"):
        article["citation"] = [c["url"] for c in discovery["citations"][:20]]
    data = [article, *entity_nodes]
    if discovery.get("faq"):
        data.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "@id": f"{canonical_url}#faq",
            "url": canonical_url,
            "mainEntity": [
                {"@type": "Question", "name": item["question"],
                 "acceptedAnswer": {"@type": "Answer", "text": item["answer"]}}
                for item in discovery["faq"][:20]
            ],
        })
    if site_url:
        data.append({
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": site_name or "Home", "item": site_url},
                {"@type": "ListItem", "position": 2, "name": content.title, "item": canonical_url},
            ],
        })
    return data


def _visible_discovery(content: PublicationContent) -> Tuple[str, str]:
    """(markup before the body, markup after it) from content.discovery."""
    discovery = content.discovery or {}
    before = ""
    if discovery.get("direct_answer"):
        before = f'<p id="tj-answer">{html.escape(discovery["direct_answer"])}</p>'
    after = []
    if discovery.get("summary") or discovery.get("key_takeaways"):
        items = "".join(f"<li>{html.escape(t)}</li>" for t in discovery.get("key_takeaways") or [])
        summary = f"<p>{html.escape(discovery['summary'])}</p>" if discovery.get("summary") else ""
        after.append(f'<section id="tj-summary"><h2>Key takeaways</h2>{summary}'
                     + (f"<ul>{items}</ul>" if items else "") + "</section>")
    body_lower = (content.body or "").lower()
    if discovery.get("faq") and "frequently asked" not in body_lower:
        qa = "".join(
            f"<h3>{html.escape(i['question'])}</h3><p>{html.escape(i['answer'])}</p>"
            for i in discovery["faq"]
        )
        after.append(f'<section id="tj-faq"><h2>Frequently asked questions</h2>{qa}</section>')
    return before, "".join(after)


def render_page(
    shell_html: str,
    *,
    content: PublicationContent,
    canonical_url: str,
    site_name: Optional[str] = None,
    site_url: Optional[str] = None,
    modified_time: Optional[datetime] = None,
    entity: Optional[dict] = None,
    template: Optional[str] = None,
) -> str:
    """Return *shell_html* with the article's own head tags and its text in
    the app's root element (or right after ``<body>`` if there is none).

    ``template`` (the site's own markup, see ``SeoPages(page_templates=...)``)
    replaces the default ``<article>``: ``{{tekjuice:title}}``,
    ``{{tekjuice:excerpt}}``, ``{{tekjuice:body_html}}``, ``{{tekjuice:slug}}``,
    ``{{tekjuice:canonical_url}}``."""
    title = html.escape(content.title)
    description = html.escape(_description(content), quote=True)
    url = html.escape(canonical_url, quote=True)
    page = _PAGE_SPECIFIC_HEAD.sub("", shell_html)
    # Site-wide JSON-LD from the shell describes the app, not this article.
    page = re.sub(r'<script\s+type=["\']application/ld\+json["\'][^>]*>.*?</script>\s*', "", page,
                  flags=re.I | re.S)

    if re.search(r"<title>.*?</title>", page, re.I | re.S):
        page = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", page, count=1, flags=re.I | re.S)
    json_ld = _structured_data(content, canonical_url=canonical_url, site_url=site_url,
                               site_name=site_name, modified_time=modified_time, entity=entity)
    head = "\n".join([
        f'<meta name="description" content="{description}" />',
        f'<link rel="canonical" href="{url}" />',
        f'<link rel="alternate" type="text/markdown" href="{url}.md" />',
        '<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large" />',
        '<meta property="og:type" content="article" />',
        f'<meta property="og:title" content="{title}" />',
        f'<meta property="og:description" content="{description}" />',
        f'<meta property="og:url" content="{url}" />',
        *([f'<meta property="og:site_name" content="{html.escape(site_name, quote=True)}" />'] if site_name else []),
        *([f'<meta property="article:modified_time" content="{modified_time.isoformat()}" />'] if modified_time else []),
        '<meta name="twitter:card" content="summary" />',
        f'<meta name="twitter:title" content="{title}" />',
        f'<meta name="twitter:description" content="{description}" />',
        *(
            '<script type="application/ld+json">'
            + json.dumps(item, ensure_ascii=False).replace("</", "<\\/")
            + "</script>"
            for item in json_ld
        ),
    ])
    if "</head>" in page:
        page = page.replace("</head>", head + "\n</head>", 1)
    else:
        page = head + page

    if template:
        article = fill_template(template, {
            "title": title, "excerpt": html.escape(content.excerpt or ""), "body_html": article_body_html(content),
            "slug": html.escape(content.slug), "canonical_url": html.escape(canonical_url, quote=True),
        })
    else:
        before, after = _visible_discovery(content)
        article = (f"<article><h1>{title}</h1>\n{before}\n{markdown_to_html(content.body)}\n{after}"
                   f'\n<p>Source: <a href="{url}">{url}</a></p></article>')
    root = re.search(r'(<div\s+id=["\']root["\'][^>]*>)(\s*)(</div>)', page, re.I)
    if root:
        page = page[:root.end(1)] + article + page[root.start(3):]
    elif re.search(r"<body[^>]*>", page, re.I):
        page = re.sub(r"(<body[^>]*>)", r"\1" + article.replace("\\", "\\\\"), page, count=1, flags=re.I)
    else:
        page += article
    # The article's data for the site's own app (see prerender_payload):
    # an app that reads it renders the same article on its first frame,
    # with no API round trip or loading state, so replacing the pre-rendered
    # HTML neither shifts the layout nor delays the largest paint.
    return _with_data(page, PRERENDER_DATA_ID,
                      prerender_payload(content, canonical_url=canonical_url, modified_time=modified_time))


PRERENDER_DATA_ID = "tekjuice-content"
_PLACEHOLDER = re.compile(r"\{\{tekjuice:([a-z_]+)\}\}")


def fill_template(template: str, values: Dict[str, str]) -> str:
    """Replace ``{{tekjuice:name}}`` placeholders (one pass: values, which are
    already escaped HTML, are never scanned for placeholders)."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), template)


def article_body_html(content: PublicationContent) -> str:
    """The article body as escaped HTML, with its visible discovery blocks
    (summary, key takeaways, FAQ): what the pre-rendered page shows."""
    before, after = _visible_discovery(content)
    return "\n".join(part for part in (before, markdown_to_html(content.body), after) if part)
HUB_DATA_ID = "tekjuice-hub"


def _script_json(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False)
    # Nothing in the text can close the script element or break JS parsing.
    return (text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _with_data(page: str, element_id: str, data: str) -> str:
    tag = f'<script type="application/json" id="{element_id}">{data}</script>'
    if re.search(r"</body>", page, re.I):
        return re.sub(r"</body>", lambda _m: tag + "\n</body>", page, count=1, flags=re.I)
    return page + tag


def prerender_payload(content: PublicationContent, *, canonical_url: str,
                      modified_time: Optional[datetime] = None) -> str:
    """JSON (safe inside a <script>) describing the pre-rendered article:
    ``slug``, ``title``, ``excerpt``, ``body`` (as stored), ``body_html`` (the
    escaped HTML the pre-rendered page shows), ``meta_description``,
    ``meta_keywords``, ``canonical_url``, ``modified``."""
    data = {
        "slug": content.slug, "title": content.title, "excerpt": content.excerpt, "body": content.body,
        # The body as the pre-rendered page shows it (escaped HTML): an app can
        # display it without shipping its own Markdown renderer to readers.
        "body_html": article_body_html(content),
        "meta_description": content.meta_description, "meta_keywords": list(content.meta_keywords),
        "canonical_url": canonical_url, "modified": modified_time.isoformat() if modified_time else None,
    }
    return _script_json(data)


def render_hub(
    shell_html: str,
    *,
    hub_url: str,
    title: str,
    description: str,
    items: Sequence[tuple],
    site_name: Optional[str] = None,
    template: Optional[str] = None,
    item_template: Optional[str] = None,
) -> str:
    """The site's article index (e.g. /resources) as server-rendered HTML:
    its own title, description and self-canonical, and a plain link to every
    published article, so crawlers that do not run JavaScript can reach them.
    ``items`` are (url, title, summary) tuples."""
    t, d, u = html.escape(title), html.escape(description, quote=True), html.escape(hub_url, quote=True)
    page = _PAGE_SPECIFIC_HEAD.sub("", shell_html)
    page = re.sub(r'<script\s+type=["\']application/ld\+json["\'][^>]*>.*?</script>\s*', "", page,
                  flags=re.I | re.S)
    page = re.sub(r'<meta\s+name=["\']robots["\'][^>]*>\s*', "", page, flags=re.I)
    if re.search(r"<title>.*?</title>", page, re.I | re.S):
        page = re.sub(r"<title>.*?</title>", f"<title>{t}</title>", page, count=1, flags=re.I | re.S)
    json_ld = {
        "@context": "https://schema.org", "@type": "CollectionPage", "name": title, "url": hub_url,
        "description": description,
        "mainEntity": {"@type": "ItemList", "numberOfItems": len(items), "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "url": url, "name": name}
            for i, (url, name, _) in enumerate(items)]},
        **({"publisher": {"@type": "Organization", "name": site_name}} if site_name else {}),
    }
    head = "\n".join([
        f'<meta name="description" content="{d}" />',
        f'<link rel="canonical" href="{u}" />',
        '<meta name="robots" content="index, follow" />',
        '<meta property="og:type" content="website" />',
        f'<meta property="og:title" content="{t}" />',
        f'<meta property="og:description" content="{d}" />',
        f'<meta property="og:url" content="{u}" />',
        '<script type="application/ld+json">' + json.dumps(json_ld, ensure_ascii=False).replace("</", "<\\/")
        + "</script>",
    ])
    page = page.replace("</head>", head + "\n</head>", 1) if "</head>" in page else head + page
    entries = "".join(
        f'<li><a href="{html.escape(url, quote=True)}">{html.escape(name)}</a>'
        + (f"<p>{html.escape(summary)}</p>" if summary else "") + "</li>"
        for url, name, summary in items)
    body = f"<main><h1>{t}</h1>\n<p>{html.escape(description)}</p>\n<ul>{entries}</ul></main>"
    if template and item_template:
        cards = "".join(fill_template(item_template, {
            "url": html.escape(url, quote=True), "slug": html.escape(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]),
            "title": html.escape(name), "summary": html.escape(summary or "")}) for url, name, summary in items)
        body = fill_template(template, {"items": cards, "title": t, "description": html.escape(description)})
    root = re.search(r'(<div\s+id=["\']root["\'][^>]*>)(\s*)(</div>)', page, re.I)
    if root:
        page = page[:root.end(1)] + body + page[root.start(3):]
    elif re.search(r"<body[^>]*>", page, re.I):
        page = re.sub(r"(<body[^>]*>)", r"\1" + body.replace("\\", "\\\\"), page, count=1, flags=re.I)
    else:
        page += body
    # The list for the site's app (first frame without an API round trip).
    data = {"title": title, "description": description, "url": hub_url,
            "items": [{"url": url, "slug": urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1], "title": name,
                       "summary": summary} for url, name, summary in items]}
    return _with_data(page, HUB_DATA_ID, _script_json(data))


# ---------------------------------------------------------------------------
# Machine-readable outputs for AI systems (LEO)
# ---------------------------------------------------------------------------

# Crawlers of AI assistants and answer engines. A specific group overrides
# the "*" group, so each repeats the site's private paths.
AI_CRAWLERS = (
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "Claude-User",
    "anthropic-ai", "PerplexityBot", "Perplexity-User", "Google-Extended", "Applebot-Extended",
    "Bingbot", "CCBot", "meta-externalagent", "Amazonbot", "DuckAssistBot", "cohere-ai",
)


def render_markdown(
    content: PublicationContent,
    *,
    canonical_url: str,
    site_name: Optional[str] = None,
    modified_time: Optional[datetime] = None,
) -> str:
    """The article as Markdown for AI systems, naming its canonical URL."""
    discovery = content.discovery or {}

    def yaml(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    front = ["---", f"title: {yaml(content.title)}", f"canonical: {canonical_url}"]
    if site_name:
        front.append(f"publisher: {yaml(site_name)}")
    if modified_time:
        front.append(f"updated: {modified_time.date().isoformat()}")
    summary = discovery.get("summary") or _description(content)
    if summary:
        front.append(f"summary: {yaml(summary)}")
    front.append("---")
    parts = ["\n".join(front), f"# {content.title}",
             f"Source: [{canonical_url}]({canonical_url})" + (f", published by {site_name}" if site_name else "")]
    if discovery.get("direct_answer"):
        parts.append(f"**Answer:** {discovery['direct_answer']}")
    parts.append((content.body or "").strip())
    if discovery.get("key_takeaways"):
        parts.append("## Key takeaways\n\n" + "\n".join(f"* {t}" for t in discovery["key_takeaways"]))
    if discovery.get("faq") and "frequently asked" not in (content.body or "").lower():
        parts.append("## Frequently asked questions\n\n" + "\n\n".join(
            f"### {i['question']}\n\n{i['answer']}" for i in discovery["faq"]))
    return "\n\n".join(parts) + "\n"


def indexnow_key(receiver_secret: str) -> str:
    """The site's IndexNow key: 32 hex characters derived from the receiver
    secret (one-way; the Data Engine derives the same key)."""
    return hmac.new(receiver_secret.encode("utf-8"), b"tekjuice-indexnow", hashlib.sha256).hexdigest()[:32]


def build_robots_txt(site_url: str, *, private_paths: Sequence[str] = ()) -> str:
    """robots.txt that welcomes search and AI crawlers to public content."""
    site = site_url.rstrip("/")
    rules = ["Allow: /", *(f"Disallow: {p}" for p in private_paths)]
    groups = ["User-agent: *\n" + "\n".join(rules)]
    groups += [f"User-agent: {agent}\n" + "\n".join(rules) for agent in AI_CRAWLERS]
    return ("\n\n".join(groups)
            + f"\n\nSitemap: {site}/sitemap.xml\n"
            + f"# AI-readable index of this site: {site}/llms.txt\n")


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
    """Discoverability outputs for a content store.

    ``shell_html`` returns the site's normal HTML page (its ``index.html``),
    so styles and the app still load for visitors. ``static_paths`` are the
    site's own public pages to list in the sitemap ("/", "/features", ...);
    ``private_paths`` are app areas crawlers should not visit.
    ``hub_path`` (e.g. "/resources"), when set, is served as a server-rendered
    index of every published article with its own canonical; ``hub_title`` and
    ``hub_description`` describe it.
    """

    def __init__(
        self,
        store,
        *,
        site_url: str,
        shell_html: Callable[[], str],
        static_paths: Sequence[str] = ("/",),
        site_name: Optional[str] = None,
        site_description: Optional[str] = None,
        private_paths: Sequence[str] = (),
        hub_path: Optional[str] = None,
        hub_title: Optional[str] = None,
        hub_description: Optional[str] = None,
        page_templates: Optional[Callable[[], Optional[Dict[str, str]]]] = None,
    ) -> None:
        """``page_templates`` (optional): returns the site's own markup for
        pre-rendered pages, ``{"article": ..., "hub": ..., "hub_item": ...}``
        with ``{{tekjuice:*}}`` placeholders (see render_page/render_hub). When
        it is the markup the site's app renders, the app's first frame
        repaints the same elements: no layout shift, and the largest paint
        does not wait for the app's scripts. Missing templates fall back to
        the default markup."""
        if not callable(getattr(store, "get_content", None)):
            raise TypeError("The content store must implement get_content(slug) to pre-render pages")
        self._store = store
        self._site = site_url.rstrip("/")
        self._shell = shell_html
        self._static = tuple(static_paths)
        self._site_name = site_name
        self._site_description = site_description
        self._private = tuple(private_paths)
        self._hub = ("/" + hub_path.strip("/")) if hub_path and hub_path.strip("/") else None
        self._hub_title = hub_title or "Articles"
        self._hub_description = hub_description or ""
        self._indexnow_key: Optional[str] = None
        self._templates_source = page_templates

    def _template(self, name: str) -> Optional[str]:
        if self._templates_source is None:
            return None
        try:
            templates = self._templates_source() or {}
        except Exception:  # noqa: BLE001 - an unavailable template falls back to the default markup
            return None
        value = templates.get(name)
        return value if isinstance(value, str) and "{{tekjuice:" in value else None

    def set_indexnow_key(self, key: str) -> None:
        """Called by PublicationReceiver; the key is served at /<key>.txt."""
        self._indexnow_key = key

    def indexnow_key_file(self, key: str) -> Optional[str]:
        """Body of ``/<key>.txt`` for the site's IndexNow key, else ``None``."""
        if self._indexnow_key and hmac.compare_digest(key, self._indexnow_key):
            return self._indexnow_key
        return None

    # -- helpers ------------------------------------------------------------

    def _published(self) -> List[PublishedPage]:
        lister = getattr(self._store, "list_published", None)
        return list(lister()) if callable(lister) else []

    def _modified(self, slug: str) -> Optional[datetime]:
        return next((p.updated_at for p in self._published() if p.slug == slug), None)

    def canonical_url(self, slug: str) -> str:
        return self._store.public_url(slug)

    def entity(self) -> dict:
        """The business's verified facts, from the most recent publication
        that carried them (``content.discovery.entity``); empty if none."""
        pages = sorted(self._published(), key=lambda p: p.updated_at or datetime.min, reverse=True)
        for page in pages:
            content = self._store.get_content(page.slug)
            entity = ((content.discovery or {}).get("entity") if content else None) or {}
            if entity.get("name"):
                return {**entity, "website": self._site + "/"}
        return {}

    # -- outputs ------------------------------------------------------------

    # -- site overrides (site.changes) -------------------------------------

    def _override(self, path: str) -> Optional[dict]:
        getter = getattr(self._store, "get_site_override", None)
        return getter(path) if callable(getter) else None

    def redirect_for(self, path: str) -> Optional[tuple]:
        """(absolute target URL, status code) when *path* is redirected."""
        override = self._override("/" + path.strip("/") if path.strip("/") else "/")
        redirect = (override or {}).get("redirect")
        if not redirect:
            return None
        return self._site + redirect["target"], int(redirect.get("status_code", 301))

    def _override_page(self, path: str, meta: dict) -> str:
        """The site's own page with overridden head metadata (title,
        description, canonical, robots); the body is the site's own."""
        page = _PAGE_SPECIFIC_HEAD.sub("", self._shell())
        page = re.sub(r'<meta\s+name=["\']robots["\'][^>]*>\s*', "", page, flags=re.I)
        tags = []
        if meta.get("title"):
            if re.search(r"<title>.*?</title>", page, re.I | re.S):
                page = re.sub(r"<title>.*?</title>", f"<title>{html.escape(meta['title'])}</title>", page,
                              count=1, flags=re.I | re.S)
            else:
                tags.append(f"<title>{html.escape(meta['title'])}</title>")
        if meta.get("meta_description"):
            tags.append(f'<meta name="description" content="{html.escape(meta["meta_description"], quote=True)}" />')
        canonical = meta.get("canonical") or (self._site + path)
        tags.append(f'<link rel="canonical" href="{html.escape(canonical, quote=True)}" />')
        if meta.get("robots"):
            tags.append(f'<meta name="robots" content="{html.escape(meta["robots"], quote=True)}" />')
        head = "\n".join(tags)
        return page.replace("</head>", head + "\n</head>", 1) if "</head>" in page else head + page

    def page(self, slug: str) -> Optional[str]:
        """Complete HTML for a published article, the site's own page with an
        approved metadata override, or ``None``."""
        slug = slug.strip("/")
        if self._hub and "/" + slug == self._hub and not self._override(self._hub):
            return self.hub()
        content = self._store.get_content(slug) if slug else None
        if content is None:
            path = "/" + slug if slug else "/"
            meta = (self._override(path) or {}).get("meta")
            return self._override_page(path, meta) if meta else None
        return render_page(
            self._shell(),
            content=content,
            canonical_url=self.canonical_url(slug),
            site_name=self._site_name,
            site_url=self._site + "/",
            modified_time=self._modified(slug),
            entity=self.entity(),
            template=self._template("article"),
        )

    def hub(self) -> Optional[str]:
        """The article index page at ``hub_path``, or None when not configured."""
        if not self._hub:
            return None
        items = []
        for page, content in self._articles():
            summary = (content.discovery or {}).get("summary") or _description(content)
            items.append((self.canonical_url(page.slug), content.title, (summary or "")[:220]))
        return render_hub(self._shell(), hub_url=self._site + self._hub, title=self._hub_title,
                          description=self._hub_description, items=items, site_name=self._site_name,
                          template=self._template("hub"), item_template=self._template("hub_item"))

    def markdown(self, slug: str) -> Optional[str]:
        content = self._store.get_content(slug)
        if content is None:
            return None
        return render_markdown(content, canonical_url=self.canonical_url(slug),
                               site_name=self._site_name, modified_time=self._modified(slug))

    def sitemap(self) -> str:
        def listed(path: str) -> bool:
            override = self._override(path.rstrip("/") or "/") or {}
            robots = (override.get("meta") or {}).get("robots") or ""
            return (not override.get("redirect") and "noindex" not in robots
                    and override.get("sitemap") != "exclude")

        entries: List[Tuple[str, Optional[datetime]]] = [
            (self._site + (path if path.startswith("/") else "/" + path), None)
            for path in self._static if listed(path if path.startswith("/") else "/" + path)
        ]
        entries += [(self.canonical_url(p.slug), p.updated_at) for p in self._published() if listed("/" + p.slug)]
        # Approved inclusions (site.changes.sitemap): the site's own pages,
        # without an invented lastmod.
        known = {url for url, _ in entries}
        lister = getattr(self._store, "list_site_overrides", None)
        for path, override in sorted((lister() if callable(lister) else {}).items()):
            url = self._site + path
            if (override or {}).get("sitemap") == "include" and url not in known and listed(path):
                entries.append((url, None))
        return build_sitemap(entries)

    def _articles(self) -> List[Tuple[PublishedPage, PublicationContent]]:
        out = []
        for page in self._published():
            content = self._store.get_content(page.slug)
            if content is not None:
                out.append((page, content))
        return out

    def llms_txt(self) -> str:
        """llms.txt: what the site is and where its articles live."""
        entity = self.entity()
        lines = [f"# {entity.get('name') or self._site_name or self._site}"]
        description = entity.get("description") or self._site_description
        if description:
            lines += ["", f"> {description}"]
        about = [(label, entity.get(key)) for label, key in (
            ("Category", "product_type"), ("Products", "products"), ("Features", "features"),
            ("Use cases", "use_cases"), ("For", "audiences"), ("Locations", "locations"),
            ("Pricing (as stated on this website)", "pricing"))]
        about = [(label, value) for label, value in about if value]
        if about:
            lines += ["", "## About", ""] + [
                f"- {label}: {'; '.join(value) if isinstance(value, list) else value}" for label, value in about]
            lines.append(f"- Facts as structured data: {self._site}/entity.json")
        lines += ["", f"Website: {self._site}/",
                  f"Full text of all articles: {self._site}/llms-full.txt", "", "## Articles", ""]
        for page, content in self._articles():
            summary = (content.discovery or {}).get("summary") or _description(content)
            lines.append(f"- [{content.title}]({self.canonical_url(page.slug)})"
                         + (f": {summary[:200]}" if summary else ""))
        return "\n".join(lines) + "\n"

    def llms_full_txt(self) -> str:
        return "\n\n---\n\n".join(
            render_markdown(content, canonical_url=self.canonical_url(page.slug),
                            site_name=self._site_name, modified_time=page.updated_at)
            for page, content in self._articles()
        )

    def feed_json(self) -> str:
        """JSON Feed 1.1 of the published articles."""
        items = []
        for page, content in self._articles():
            url = self.canonical_url(page.slug)
            item = {
                "id": url, "url": url, "title": content.title,
                "summary": (content.discovery or {}).get("summary") or _description(content),
                "content_text": content.body,
            }
            if page.updated_at:
                item["date_modified"] = page.updated_at.isoformat()
            if (content.discovery or {}).get("entities"):
                item["tags"] = content.discovery["entities"][:12]
            items.append(item)
        return json.dumps({
            "version": "https://jsonfeed.org/version/1.1",
            "title": self._site_name or self._site,
            "home_page_url": self._site + "/",
            "feed_url": self._site + "/feed.json",
            "items": items,
        }, ensure_ascii=False, indent=2)

    def entity_json(self) -> Optional[str]:
        """The verified facts as JSON (schema.org nodes plus plain facts),
        or ``None`` before any publication carried them."""
        entity = self.entity()
        if not entity:
            return None
        return json.dumps({
            "source": self._site + "/",
            "facts": entity,
            "json_ld": entity_json_ld(entity, site_url=self._site + "/"),
        }, ensure_ascii=False, indent=2)

    def robots_txt(self) -> str:
        return build_robots_txt(self._site, private_paths=self._private)
