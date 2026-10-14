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
