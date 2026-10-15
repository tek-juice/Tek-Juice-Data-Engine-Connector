"""site.changes: approved metadata overrides and redirects on the customer's pages."""

import json

import pytest

from tekjuice_connector.publication import InMemoryContentStore, PublicationContent, PublicationReceiver, SeoPages, SQLiteContentStore
from tekjuice_connector.publication.errors import ContractViolationError
from tekjuice_connector.publication.site_changes import parse_site_change

SECRET = "s" * 40
SHELL = ('<!doctype html><html><head><title>App</title><link rel="canonical" href="https://site.example/" />'
         '<meta name="robots" content="noindex" /></head><body><div id="root"></div></body></html>')


def _setup(store=None):
    store = store or InMemoryContentStore("https://site.example")
    store.create(PublicationContent(title="Budget guide", body="Text.", slug="budget-guide"),
                 execution_id="e1", idempotency_key="k1")
    store.create(PublicationContent(title="Old guide", body="Text.", slug="old-guide"),
                 execution_id="e2", idempotency_key="k2")
    seo = SeoPages(store, site_url="https://site.example", shell_html=lambda: SHELL, static_paths=("/", "/resources"))
    receiver = PublicationReceiver(store=store, website_id="w", receiver_secret=SECRET, seo=seo)
    return store, seo, receiver


def _send(receiver, change, key="change-key-1"):
    body = json.dumps({"contract_version": "1.0", "action": "site_change", "website_id": "w",
                       "idempotency_key": key, "change": change}).encode()
    return receiver.handle({"authorization": f"Bearer {SECRET}", "x-tekjuice-contract-version": "1.0"}, body)


def test_capability_only_with_seo_and_override_storage():
    store, _, receiver = _setup()
    assert "site.changes" in receiver.capabilities
    plain = PublicationReceiver(store=store, website_id="w", receiver_secret=SECRET)
    assert "site.changes" not in plain.capabilities
    assert _send(plain, {"type": "remove", "path": "/x"}).status_code in (400, 422, 501)


def test_page_meta_override_fixes_a_customer_page_and_is_reversible():
    _, seo, receiver = _setup()
    response = _send(receiver, {"type": "page_meta", "path": "/resources", "canonical": "/resources",
                                "robots": "index,follow", "title": "Resources"})
    assert response.status_code == 200, response.body
    assert response.body["previous"] is None
    page = seo.page("resources")
    assert '<link rel="canonical" href="https://site.example/resources" />' in page
    assert page.count('rel="canonical"') == 1 and "noindex" not in page and "<title>Resources</title>" in page
    assert seo.page("dashboard") is None            # untouched pages fall back to the app
    # Replay is idempotent; rollback restores the previous state.
    assert _send(receiver, {"type": "page_meta", "path": "/resources", "title": "Resources"}).body["replayed"] is True
    removed = _send(receiver, {"type": "remove", "path": "/resources"}, key="change-key-2")
    assert removed.body["previous"]["meta"]["canonical"] == "https://site.example/resources"
    assert seo.page("resources") is None


def test_redirects_consolidate_without_loops_or_chains(tmp_path):
    store, seo, receiver = _setup(SQLiteContentStore(str(tmp_path / "c.db"), "https://site.example"))
    ok = _send(receiver, {"type": "redirect", "path": "/old-guide", "target": "https://site.example/budget-guide"})
    assert ok.status_code == 200
    assert seo.redirect_for("old-guide") == ("https://site.example/budget-guide", 301)
    assert "old-guide" not in seo.sitemap()
    chain = _send(receiver, {"type": "redirect", "path": "/budget-guide", "target": "/elsewhere"}, key="change-key-3")
    assert chain.status_code == 422                      # something already redirects to it
    away = _send(receiver, {"type": "redirect", "path": "/a", "target": "https://evil.example/x"}, key="change-key-4")
    assert away.status_code == 422


def test_validation():
    base = "https://site.example"
    with pytest.raises(ContractViolationError):
        parse_site_change({"idempotency_key": "key-12345", "change": {"type": "redirect", "path": "/a", "target": "/a"}}, base_url=base)
    with pytest.raises(ContractViolationError):
        parse_site_change({"idempotency_key": "key-12345", "change": {"type": "page_meta", "path": "/../etc"}}, base_url=base)
    with pytest.raises(ContractViolationError):
        parse_site_change({"idempotency_key": "key-12345", "change": {"type": "page_meta", "path": "/a", "robots": "all"}}, base_url=base)
    with pytest.raises(ContractViolationError):
        parse_site_change({"idempotency_key": "key-12345", "change": {"type": "page_meta", "path": "/a"}}, base_url=base)


def test_router_serves_redirects_and_overridden_pages():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from tekjuice_connector.adapters.fastapi import create_seo_router

    _, seo, receiver = _setup()
    _send(receiver, {"type": "redirect", "path": "/old-guide", "target": "/budget-guide", "status_code": 308})
    _send(receiver, {"type": "page_meta", "path": "/", "title": "Home"}, key="change-key-5")
    app = fastapi.FastAPI()
    app.include_router(create_seo_router(seo, prefix="/seo"))
    client = TestClient(app)
    moved = client.get("/seo/page/old-guide", follow_redirects=False)
    assert moved.status_code == 308 and moved.headers["location"] == "https://site.example/budget-guide"
    home = client.get("/seo/page/")
    assert home.status_code == 200 and "<title>Home</title>" in home.text
    assert client.get("/seo/page/nested/unknown").status_code == 404


def test_sitemap_changes_include_and_exclude_pages_and_are_reversible(tmp_path):
    for store in (None, SQLiteContentStore(str(tmp_path / "s.db"), "https://site.example")):
        store, seo, receiver = _setup(store)
        assert "site.changes.sitemap" in receiver.capabilities
        assert "https://site.example/pricing" not in seo.sitemap()
        ok = _send(receiver, {"type": "sitemap", "path": "/pricing", "sitemap": "include"})
        assert ok.status_code == 200, ok.body
        xml = seo.sitemap()
        entry = xml.split("https://site.example/pricing")[1].split("</url>")[0]
        assert "<lastmod>" not in entry               # no invented modification date
        assert _send(receiver, {"type": "sitemap", "path": "/old-guide", "sitemap": "exclude"},
                     key="change-key-2").status_code == 200
        assert "https://site.example/old-guide" not in seo.sitemap()
        removed = _send(receiver, {"type": "remove", "path": "/pricing", "remove": "sitemap"}, key="change-key-3")
        assert removed.body["previous"]["sitemap"] == "include"
        assert "https://site.example/pricing" not in seo.sitemap()


def test_sitemap_change_needs_the_sitemap_capability():
    store = InMemoryContentStore("https://site.example")
    plain = PublicationReceiver(store=store, website_id="w", receiver_secret=SECRET)
    assert "site.changes.sitemap" not in plain.capabilities
    with pytest.raises(ContractViolationError):
        parse_site_change({"idempotency_key": "k" * 10, "change": {"type": "sitemap", "path": "/x",
                                                                    "sitemap": "maybe"}},
                          base_url="https://site.example")
