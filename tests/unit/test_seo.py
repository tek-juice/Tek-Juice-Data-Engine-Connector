"""Pre-rendered article pages and sitemap (seo.prerender / seo.sitemap)."""

from datetime import datetime, timezone

import pytest

from tekjuice_connector.publication import (
    InMemoryContentStore,
    PublicationContent,
    PublicationReceiver,
    SeoPages,
    SQLiteContentStore,
    build_sitemap,
    markdown_to_html,
)

SHELL = """<!doctype html><html><head>
<title>UMET — Shell Title</title>
<meta name="description" content="Shell description" />
<meta property="og:title" content="Shell" />
<link rel="canonical" href="https://site.example/" />
<script type="module" src="/assets/index-abc.js"></script>
</head><body><div id="root"></div></body></html>"""

ARTICLE = PublicationContent(
    title="Tracking Every Expense Without the Stress",
    body=("Money disappears between pay days.\n\n## Why tracking matters\n\n"
          "See [our guide](https://site.example/guide) and <script>alert(1)</script>.\n\n"
          "* Log spending\n* Set budgets"),
    slug="tracking-every-expense",
    meta_description="A calm, practical guide to tracking every expense and setting budgets.",
)


def _seo(store):
    return SeoPages(store, site_url="https://site.example", shell_html=lambda: SHELL,
                    static_paths=("/", "/features"), site_name="UMET")


def _store():
    store = InMemoryContentStore("https://site.example")
    store.create(ARTICLE, execution_id="e1", idempotency_key="k1")
    return store


def test_page_has_its_own_head_and_visible_article():
    page = _seo(_store()).page("tracking-every-expense")
    assert "<title>Tracking Every Expense Without the Stress</title>" in page
    assert "Shell description" not in page and "UMET — Shell Title" not in page
    assert '<meta name="description" content="A calm, practical guide' in page
    assert '<link rel="canonical" href="https://site.example/tracking-every-expense" />' in page
    assert page.count('rel="canonical"') == 1
    assert '"@type": "Article"' in page
    assert '<div id="root"><article><h1>Tracking Every Expense Without the Stress</h1>' in page
    assert "<h3>Why tracking matters</h3>" in page
    assert '<a href="https://site.example/guide">our guide</a>' in page
    assert "<ul><li>Log spending</li><li>Set budgets</li></ul>" in page
    assert '<script type="module" src="/assets/index-abc.js"></script>' in page   # app still loads


def test_article_text_is_escaped():
    page = _seo(_store()).page("tracking-every-expense")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_unknown_slug_is_not_rendered():
    assert _seo(_store()).page("dashboard") is None


def test_only_http_links_become_links():
    assert "<a" not in markdown_to_html("Click [here](javascript:alert(1)).")


def test_sitemap_lists_static_pages_and_articles(tmp_path):
    store = SQLiteContentStore(str(tmp_path / "c.db"), "https://site.example")
    store.create(ARTICLE, execution_id="e1", idempotency_key="k1")
    xml = _seo(store).sitemap()
    assert "<loc>https://site.example/</loc>" in xml
    assert "<loc>https://site.example/features</loc>" in xml
    assert "<loc>https://site.example/tracking-every-expense</loc><lastmod>" in xml
    assert build_sitemap([("https://a.example/x", datetime(2026, 1, 2, tzinfo=timezone.utc))]).count("<url>") == 1


def test_capabilities_are_advertised_only_when_seo_is_mounted(tmp_path):
    store = SQLiteContentStore(str(tmp_path / "c.db"), "https://site.example")
    plain = PublicationReceiver(store=store, website_id="w", receiver_secret="s" * 40)
    assert not {"seo.prerender", "seo.sitemap"} & set(plain.capabilities)
    mounted = PublicationReceiver(store=store, website_id="w", receiver_secret="s" * 40, seo=_seo(store))
    assert {"seo.prerender", "seo.sitemap"} <= set(mounted.capabilities)


def test_fastapi_router_serves_sitemap_and_pages():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from tekjuice_connector.adapters.fastapi import create_seo_router

    app = fastapi.FastAPI()
    app.include_router(create_seo_router(_seo(_store()), prefix="/seo"))
    client = TestClient(app)
    assert client.get("/seo/sitemap.xml").headers["content-type"].startswith("application/xml")
    page = client.get("/seo/page/tracking-every-expense")
    assert page.status_code == 200 and "Tracking Every Expense" in page.text
    assert client.get("/seo/page/dashboard").status_code == 404


DISCOVERY = {
    "summary": "Tracking every expense shows where money goes and makes budgets realistic.",
    "key_takeaways": ["Log spending the day it happens", "Review categories weekly"],
    "entities": ["Expense tracking", "Budgeting"],
    "direct_answer": "Record each purchase as it happens and review your categories once a week.",
    "faq": [{"question": "How often should I review expenses?", "answer": "Once a week works for most people."}],
    "citations": [{"url": "https://www.gov.uk/budgeting", "title": "Budgeting"}],
    "json_ld": [{"@context": "https://schema.org", "@type": "Article", "url": "https://wrong.example/x",
                 "headline": "x", "wordCount": 400}],
    "language": "en-GB",
}


def _discovery_store():
    from dataclasses import replace
    store = InMemoryContentStore("https://site.example")
    store.create(replace(ARTICLE, discovery=DISCOVERY), execution_id="e1", idempotency_key="k1")
    return store


def _json_ld(page):
    import json
    import re
    return [json.loads(m) for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)]


def test_structured_data_uses_the_canonical_url_and_discovery():
    page = _seo(_discovery_store()).page("tracking-every-expense")
    data = {item["@type"]: item for item in _json_ld(page)}
    article = data["Article"]
    assert article["url"] == "https://site.example/tracking-every-expense"
    assert article["mainEntityOfPage"]["@id"] == "https://site.example/tracking-every-expense"
    assert "wrong.example" not in page
    assert article["wordCount"] == 400 and article["inLanguage"] == "en-GB"
    assert article["publisher"]["name"] == "UMET"
    assert article["speakable"]["cssSelector"] == ["#tj-answer", "#tj-summary"]
    assert article["citation"] == ["https://www.gov.uk/budgeting"]
    assert data["FAQPage"]["mainEntity"][0]["name"] == "How often should I review expenses?"
    assert data["BreadcrumbList"]["itemListElement"][1]["item"] == "https://site.example/tracking-every-expense"
    assert '<p id="tj-answer">Record each purchase' in page
    assert '<section id="tj-summary">' in page and "Review categories weekly" in page
    assert "<h3>How often should I review expenses?</h3>" in page
    assert '<link rel="alternate" type="text/markdown" href="https://site.example/tracking-every-expense.md" />' in page


def test_shell_json_ld_is_replaced():
    shell = SHELL.replace("</head>", '<script type="application/ld+json">{"@type":"SoftwareApplication"}</script></head>')
    seo = SeoPages(_store(), site_url="https://site.example", shell_html=lambda: shell)
    page = seo.page("tracking-every-expense")
    assert "SoftwareApplication" not in page
    assert [d["@type"] for d in _json_ld(page)] == ["Article", "BreadcrumbList"]


def test_markdown_names_the_canonical_source():
    md = _seo(_discovery_store()).markdown("tracking-every-expense")
    assert md.startswith("---\ntitle: \"Tracking Every Expense Without the Stress\"\n")
    assert "canonical: https://site.example/tracking-every-expense" in md
    assert "Source: [https://site.example/tracking-every-expense](https://site.example/tracking-every-expense), published by UMET" in md
    assert "**Answer:** Record each purchase" in md
    assert "## Key takeaways" in md and "## Frequently asked questions" in md
    assert _seo(_store()).markdown("dashboard") is None


def test_llms_txt_feed_and_robots():
    seo = SeoPages(_discovery_store(), site_url="https://site.example", shell_html=lambda: SHELL,
                   site_name="UMET", site_description="Expense tracker.", private_paths=("/dashboard",))
    llms = seo.llms_txt()
    assert llms.startswith("# UMET\n\n> Expense tracker.")
    assert ("- [Tracking Every Expense Without the Stress](https://site.example/tracking-every-expense): "
            "Tracking every expense shows") in llms
    assert "canonical: https://site.example/tracking-every-expense" in seo.llms_full_txt()
    import json
    feed = json.loads(seo.feed_json())
    assert feed["version"] == "https://jsonfeed.org/version/1.1"
    assert feed["items"][0]["url"] == "https://site.example/tracking-every-expense"
    robots = seo.robots_txt()
    assert "User-agent: GPTBot\nAllow: /\nDisallow: /dashboard" in robots
    assert "User-agent: ClaudeBot" in robots and "User-agent: PerplexityBot" in robots
    assert "Sitemap: https://site.example/sitemap.xml" in robots


def test_fastapi_router_serves_ai_outputs():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from tekjuice_connector.adapters.fastapi import create_seo_router

    app = fastapi.FastAPI()
    app.include_router(create_seo_router(_seo(_discovery_store()), prefix="/seo"))
    client = TestClient(app)
    md = client.get("/seo/markdown/tracking-every-expense")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert md.headers["link"] == '<https://site.example/tracking-every-expense>; rel="canonical"'
    assert client.get("/seo/markdown/nope").status_code == 404
    for path in ("llms.txt", "llms-full.txt", "robots.txt"):
        assert client.get(f"/seo/{path}").headers["content-type"].startswith("text/plain")
    assert client.get("/seo/feed.json").headers["content-type"].startswith("application/feed+json")


def test_indexnow_key_is_served_only_for_the_receiver_key():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from tekjuice_connector.adapters.fastapi import create_seo_router
    from tekjuice_connector.publication import indexnow_key

    store = _store()
    seo = _seo(store)
    assert seo.indexnow_key_file("0" * 32) is None   # no receiver yet
    receiver = PublicationReceiver(store=store, website_id="w", receiver_secret="s" * 40, seo=seo)
    assert "ai.indexnow" in receiver.capabilities
    key = indexnow_key("s" * 40)
    assert len(key) == 32 and key != "s" * 32
    app = fastapi.FastAPI()
    app.include_router(create_seo_router(seo, prefix="/seo"))
    client = TestClient(app)
    response = client.get(f"/seo/indexnow/{key}")
    assert response.status_code == 200 and response.text == key
    assert client.get(f"/seo/indexnow/{'0' * 32}").status_code == 404
