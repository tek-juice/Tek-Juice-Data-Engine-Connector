"""Content types against a real Laravel application.

Runs when ``TEKJUICE_LARAVEL_URL`` (the API base, e.g.
``http://127.0.0.1:18080/api``) and ``TEKJUICE_LARAVEL_TOKEN`` point at a
Laravel app with ``posts`` (enabled with ``x-tekjuice-content-type``) and
``products`` (not enabled) API resources and an OpenAPI document at
``/openapi.json``: Eloquent models, ``$request->validate()`` rules, JSON
resources ({"data": ...}), route-model binding. The app used for the
release check is described in docs/content-types.md ("Validating against
Laravel"). Each run uses fresh slugs, so the app's database may be reused.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
import requests

from tekjuice_connector.publication import InMemoryContentStore, PublicationReceiver, native_source, openapi_source
from tekjuice_connector.publication.content_types import ContentTypeRegistry
from tests.fixtures.publication_fixtures import AUTH_HEADERS, RECEIVER_SECRET, WEBSITE_ID
from tests.unit.test_content_types import typed_payload

BASE = os.environ.get("TEKJUICE_LARAVEL_URL", "")
TOKEN = os.environ.get("TEKJUICE_LARAVEL_TOKEN", "")

pytestmark = pytest.mark.skipif(not (BASE and TOKEN), reason="set TEKJUICE_LARAVEL_URL and TEKJUICE_LARAVEL_TOKEN")


def _receiver(tmp_path, allow=()):
    store = InMemoryContentStore("https://shop.example")
    registry = ContentTypeRegistry([
        native_source(store),
        openapi_source(BASE, document_path="/openapi.json", ledger_path=str(tmp_path / "types.sqlite3"),
                       token=TOKEN)], allow=allow)
    return PublicationReceiver(store=store, website_id=WEBSITE_ID, receiver_secret=RECEIVER_SECRET,
                               content_types=registry)


def send(receiver, payload):
    return receiver.handle(AUTH_HEADERS, json.dumps(payload).encode())


def _api(method, path, body=None):
    return requests.request(method, BASE + path, json=body, timeout=10,
                            headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"})


def _slug(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_laravel_resources_are_discovered_with_their_rules(tmp_path):
    receiver = _receiver(tmp_path)
    described = send(receiver, {"contract_version": "1.0", "action": "capabilities",
                                "website_id": WEBSITE_ID}).body["content_types"]
    types = {t["name"]: t for t in described["types"]}
    assert types["post"]["status"] == "ready" and types["post"]["identity"] == "id"
    assert types["post"]["required_fields"] == ["title", "slug", "body"]
    assert types["product"]["status"] == "validated"           # Laravel exposes it; the owner did not enable it
    assert TOKEN not in json.dumps(described)


def test_a_post_is_created_updated_and_retried_on_laravel(tmp_path):
    receiver = _receiver(tmp_path)
    slug = _slug("spring-update")
    created = send(receiver, typed_payload("post", slug=slug, key=f"k-{slug}"))
    assert created.status_code == 200, created.body
    assert created.body["published_url"] == f"https://shop.example/blog/{slug}"
    record = _api("GET", f"/posts/{created.body['resource_id']}").json()["data"]
    assert record["slug"] == slug and record["title"] == "Launch party" and record["body"].startswith("<p>")
    assert record["excerpt"] == "Join us." and record["status"] == "draft"    # Laravel's own default stands

    count = len(_api("GET", "/posts").json()["data"])
    again = send(receiver, typed_payload("post", slug=slug, key=f"k-{slug}"))
    assert again.body["replayed"] is True and len(_api("GET", "/posts").json()["data"]) == count

    updated = send(receiver, typed_payload("post", action="update", slug=slug, key=f"k2-{slug}",
                                           execution=f"e2-{slug}", title="Spring update (revised)"))
    assert updated.status_code == 200, updated.body
    assert _api("GET", f"/posts/{created.body['resource_id']}").json()["data"]["title"] == "Spring update (revised)"


def test_laravel_validation_and_conflicts_are_reported_not_overwritten(tmp_path):
    receiver = _receiver(tmp_path)
    taken = _slug("taken")
    assert _api("POST", "/posts", {"title": "Someone else's post", "slug": taken, "body": "<p>Theirs.</p>"}).status_code == 201
    conflict = send(receiver, typed_payload("post", slug=taken, key=f"k-{taken}"))
    assert conflict.status_code == 422 and "The slug has already been taken." in conflict.body["error"]["message"]
    theirs = [p for p in _api("GET", "/posts").json()["data"] if p["slug"] == taken]
    assert len(theirs) == 1 and theirs[0]["title"] == "Someone else's post"      # never overwritten

    too_long = send(receiver, typed_payload("post", slug=_slug("long"), key="k-long", title="x" * 130))
    assert too_long.status_code == 422 and too_long.body["error"]["code"] == "content_mapping_failed"

    bad_status = send(receiver, typed_payload("post", slug=_slug("status"), key="k-status",
                                              fields={"status": "archived"}))
    assert bad_status.status_code == 422 and "must be one of ['draft', 'published']" in bad_status.body["error"]["message"]


def test_products_need_enabling_and_then_follow_laravel_rules(tmp_path):
    slug = _slug("mug")
    refused = send(_receiver(tmp_path), typed_payload("product", slug=slug, key=f"k-{slug}",
                                                      fields={"price": 9.5, "sku": slug.upper()}))
    assert refused.status_code == 422 and refused.body["error"]["code"] == "content_type_unavailable"
    assert not [p for p in _api("GET", "/products").json()["data"] if p["slug"] == slug]

    enabled = _receiver(tmp_path, allow=["product"])
    missing = send(enabled, typed_payload("product", slug=slug, key=f"k1-{slug}", fields={"price": 9.5}))
    assert missing.status_code == 422 and missing.body["error"]["mapping"]["missing_required"] == ["sku"]
    made = send(enabled, typed_payload("product", slug=slug, key=f"k2-{slug}", fields={"price": 9.5, "sku": slug.upper()}))
    assert made.status_code == 200, made.body
    product = [p for p in _api("GET", "/products").json()["data"] if p["slug"] == slug][0]
    assert product["name"] == "Launch party" and product["price"] == 9.5 and product["description"].startswith("<p>")


def test_a_wrong_token_is_refused_by_laravel(tmp_path):
    registry = ContentTypeRegistry([openapi_source(BASE, document_path="/openapi.json",
                                                   ledger_path=str(tmp_path / "t.sqlite3"), token="wrong-" + "x" * 40)],
                                   default=None)
    described = registry.describe()
    assert described["types"] == [] and "refused the credential" in described["errors"][0]
