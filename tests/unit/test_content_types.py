"""Content types: discovery, the capability map, schema mapping and typed publication."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from tekjuice_connector.publication import (
    InMemoryContentStore,
    PublicationContent,
    PublicationReceiver,
    capabilities_from_openapi,
    content_api_source,
    map_publication,
    native_source,
    openapi_source,
    wordpress_source,
)
from tekjuice_connector.publication.content_types import (
    READY,
    UNSUPPORTED,
    VALIDATED,
    ContentTypeRegistry,
    classify,
)
from tekjuice_connector.publication.content_sources import ContentSourceError
from tests.fixtures.publication_fixtures import AUTH_HEADERS, RECEIVER_SECRET, WEBSITE_ID

BASE = "http://backend:3000/api"
TOKEN = "content-api-token-" + "t" * 32


class FakeBackend:
    """A REST backend in memory: records per collection, Laravel-style
    responses ({"data": {...}}, 422 {"message", "errors": {field: [...]}})."""

    def __init__(self, manifest: Optional[Dict[str, Any]] = None, openapi: Optional[Dict[str, Any]] = None,
                 required: Optional[Dict[str, List[str]]] = None, wrap: bool = True) -> None:
        self.manifest = manifest
        self.openapi = openapi
        self.required = required or {}
        self.wrap = wrap
        self.records: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.calls: List[tuple] = []
        self.fail_with: Optional[int] = None
        self.raise_error: Optional[Exception] = None
        self.next_id = 100

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if self.raise_error:
            raise self.raise_error
        if headers.get("Authorization") not in (f"Bearer {TOKEN}",) and not headers.get(
                "Authorization", "").startswith("Basic "):
            return 401, {"message": "Unauthenticated."}
        path = url[len(BASE):] if url.startswith(BASE) else url.split("/wp-json", 1)[-1]
        if path == "/capabilities":
            return (200, self.manifest) if self.manifest is not None else (404, None)
        if path == "/openapi.json":
            return (200, self.openapi) if self.openapi is not None else (404, None)
        if self.fail_with:
            return self.fail_with, {"message": "Server Error"}
        parts = [p for p in path.split("/") if p]
        collection = parts[0]
        records = self.records.setdefault(collection, {})
        if method == "POST" and len(parts) == 1:
            missing = [f for f in self.required.get(collection, []) if f not in (body or {})]
            if missing:
                return 422, {"message": "The given data was invalid.",
                             "errors": {f: [f"The {f} field is required."] for f in missing}}
            if any(r.get("slug") == body.get("slug") for r in records.values()):
                return 409, {"message": "slug taken"}
            self.next_id += 1
            record = {"id": self.next_id, **body, "url": f"https://www.customer.example/{collection}/{body.get('slug')}"}
            records[str(self.next_id)] = record
            return 201, {"data": record} if self.wrap else record
        if method in ("PUT", "PATCH") and len(parts) == 2:
            key = parts[1]
            target = records.get(key) or next((r for r in records.values() if r.get("slug") == key), None)
            if target is None:
                return 404, {"message": "Not found"}
            target.update(body or {})
            return 200, {"data": target} if self.wrap else target
        if method == "GET" and len(parts) == 2:
            target = records.get(parts[1]) or next((r for r in records.values() if r.get("slug") == parts[1]), None)
            return (200, target) if target else (404, None)
        return 405, None


POST_SCHEMA = {"type": "object", "required": ["title", "content", "slug"], "properties": {
    "title": {"type": "string", "maxLength": 200}, "content": {"type": "string", "format": "html"},
    "slug": {"type": "string"}, "summary": {"type": "string"},
    "status": {"type": "string", "enum": ["draft", "published"], "default": "draft"},
    "id": {"type": "integer", "readOnly": True}}}
EVENT_SCHEMA = {"type": "object", "required": ["name", "slug", "starts_at", "venue"], "properties": {
    "name": {"type": "string"}, "slug": {"type": "string"}, "description": {"type": "string", "format": "html"},
    "starts_at": {"type": "string", "format": "date-time"}, "capacity": {"type": "integer", "minimum": 1},
    "is_online": {"type": "boolean"},
    "venue": {"type": "object", "required": ["city"], "properties": {"city": {"type": "string"},
                                                                       "address": {"type": "string"}}},
    "cover_image": {"type": "string", "format": "uri"}}}
ISSUE_SCHEMA = {"type": "object", "required": ["subject", "html", "slug"], "properties": {
    "subject": {"type": "string"}, "html": {"type": "string", "format": "html"}, "slug": {"type": "string"},
    "preheader": {"type": "string"}}}


def manifest(*types):
    return {"update": True, "public_url_template": "https://www.customer.example/blog/{slug}",
            "content_types": list(types)}


EVENTS = {"name": "event", "label": "Events", "schema": EVENT_SCHEMA, "identity": "slug",
          "public_url_template": "https://www.customer.example/events/{slug}",
          "operations": {"create": {"method": "POST", "path": "/events"},
                         "update": {"method": "PATCH", "path": "/events/{slug}"}}}
ISSUES = {"name": "newsletter_issue", "label": "Newsletter issues", "schema": ISSUE_SCHEMA, "public": False,
          "operations": {"create": {"method": "POST", "path": "/issues"},
                         "send": {"method": "POST", "path": "/issues/{slug}/send"}}}


def registry(backend, tmp_path, *, store=None, allow=(), extra=()):
    sources = [native_source(store or InMemoryContentStore("https://www.customer.example"))]
    sources.append(content_api_source(BASE, ledger_path=str(tmp_path / "ledger.db"), token=TOKEN, request=backend))
    sources.extend(extra)
    return ContentTypeRegistry(sources, allow=allow)


def typed_payload(content_type: Optional[str], *, action="create", slug="launch-party", fields=None,
                  title="Launch party", body="<p>Join us for the launch.</p>", key="key-1", execution="exec-1"):
    content = PublicationContent(title=title, body=body, slug=slug, excerpt="Join us.",
                                 meta_description="The launch.", meta_keywords=["launch"],
                                 content_type=content_type, fields=fields)
    data = {"title": title, "body": body, "slug": slug, "excerpt": "Join us.", "meta_description": "The launch.",
            "meta_keywords": ["launch"]}
    if content_type:
        data["content_type"] = content_type
    if fields is not None:
        data["fields"] = fields
    return {"contract_version": "1.0", "execution_id": execution, "website_id": WEBSITE_ID, "action": action,
            "idempotency_key": key, "content": data, "metadata": {"content_hash": content.content_hash()}}


def send(receiver, payload):
    return receiver.handle(AUTH_HEADERS, json.dumps(payload).encode())


# ── Discovery and the capability map ────────────────────────────────────────

def test_a_backend_with_one_type_needs_no_other(tmp_path):
    backend = FakeBackend(manifest=None)
    reg = registry(backend, tmp_path)
    assert reg.names() == ["article"] and reg.get("article").status == READY
    assert reg.describe()["ready"] == ["article"]


def test_several_types_and_an_unfamiliar_custom_type(tmp_path):
    horse = {"name": "horse_listing", "label": "Horses for sale", "schema": {
        "type": "object", "required": ["name", "slug", "breed"], "properties": {
            "name": {"type": "string"}, "slug": {"type": "string"}, "breed": {"type": "string"},
            "body": {"type": "string", "format": "html"}}},
        "operations": {"create": {"path": "/horses"}}}
    reg = registry(FakeBackend(manifest=manifest(EVENTS, ISSUES, horse)), tmp_path)
    assert reg.names() == ["article", "event", "horse_listing", "newsletter_issue"]
    assert all(reg.get(n).status == READY for n in reg.names())
    assert reg.get("event").category == "events" and reg.get("horse_listing").category == "commerce"
    assert reg.get("newsletter_issue").to_dict()["executable_operations"] == ["create"]   # send is never executed
    assert "send" in reg.get("newsletter_issue").operations


def test_no_discoverable_types_and_auth_failures_are_reported(tmp_path):
    reg = ContentTypeRegistry([content_api_source(BASE, ledger_path=str(tmp_path / "l.db"), token="wrong-token-" + "x" * 30,
                                                  request=FakeBackend(manifest=manifest(EVENTS)))], default=None)
    described = reg.describe()
    assert described["types"] == [] and "refused the credential" in described["errors"][0]
    with pytest.raises(Exception):
        reg.resolve("event", "create")


def test_incomplete_and_contradictory_schemas_are_unsupported(tmp_path):
    broken = {"name": "case_study", "schema": {"type": "object", "required": ["slug", "id"], "properties": {
        "slug": {"type": "string"}, "id": {"type": "integer", "readOnly": True}}},
        "operations": {"create": {"path": "/cases"}}}
    no_schema = {"name": "faq", "operations": {"create": {"path": "/faqs"}}}
    reg = registry(FakeBackend(manifest=manifest(broken, no_schema)), tmp_path)
    assert reg.get("case_study").status == UNSUPPORTED
    assert any("required and read-only" in l for l in reg.get("case_study").limitations)
    assert reg.get("faq").status == UNSUPPORTED and "no schema" in reg.get("faq").limitations[0]


def test_capabilities_change_after_discovery(tmp_path):
    now = [0.0]
    backend = FakeBackend(manifest=manifest(EVENTS))
    sources = [content_api_source(BASE, ledger_path=str(tmp_path / "l.db"), token=TOKEN, request=backend)]
    reg = ContentTypeRegistry(sources, default=None, ttl=60, clock=lambda: now[0])
    first = reg.get("event").schema_version
    backend.manifest = manifest(dict(EVENTS, schema={**EVENT_SCHEMA, "required": [*EVENT_SCHEMA["required"], "capacity"]}),
                                ISSUES)
    assert reg.names() == ["event"]          # cached
    now[0] = 61.0
    assert reg.names() == ["event", "newsletter_issue"]
    assert reg.get("event").schema_version != first
    backend.manifest = manifest()
    reg.refresh()
    assert reg.names() == []                 # removed types disappear


def test_cross_host_and_unsafe_endpoints_are_refused(tmp_path):
    evil = dict(EVENTS, name="evil", operations={"create": {"path": "https://attacker.example/steal"}})
    climb = dict(EVENTS, name="climb", operations={"create": {"path": "/../admin"}})
    for entry in (evil, climb):
        reg = registry(FakeBackend(manifest=manifest(entry)), tmp_path)
        assert reg.names() == ["article"] and "must be a path on the configured backend" in reg.describe()["errors"][0]
    with pytest.raises(ContentSourceError):
        content_api_source("http://public.example.com/api", ledger_path=str(tmp_path / "l.db"),
                           request=FakeBackend())()


def test_taxonomy_is_an_aid_not_an_allow_list():
    assert classify("blog_posts") == "editorial" and classify("products") == "commerce"
    assert classify("newsletter_issue") == "newsletter" and classify("webinars") == "events"
    assert classify("zebra_crossing_survey") == "community" and classify("frobnicator") is None


# ── Schema mapping ───────────────────────────────────────────────────────────

def _capability(schema, **extra):
    from tekjuice_connector.publication.content_sources import capability_from_manifest

    return capability_from_manifest({"name": "thing", "schema": schema, "operations": {"create": {"path": "/x"}},
                                     **extra}, adapter="content_api", evidence="test")


def _content(**fields):
    return PublicationContent(title="Launch party", body="<p>Join <b>us</b>.</p>", slug="launch-party",
                              excerpt="Join us.", meta_description="The launch.", meta_keywords=["launch", "party"],
                              content_type="thing", fields=fields or None)


def test_standard_and_custom_fields_are_mapped():
    result = map_publication(_capability(EVENT_SCHEMA), _content(
        starts_at="2026-11-01T18:00:00+00:00", capacity="120", is_online=False, venue={"city": "Kampala"},
        cover_image="https://cdn.customer.example/launch.jpg", sponsor="ACME"))
    assert result.ok, result.report()
    assert result.payload["name"] == "Launch party" and result.payload["description"] == "<p>Join <b>us</b>.</p>"
    assert result.payload["capacity"] == 120 and result.payload["venue"] == {"city": "Kampala"}
    assert result.mapped["name"] == "title" and result.mapped["starts_at"] == "fields.starts_at"
    assert "fields.sponsor" in result.unmapped_sources and "excerpt" in result.unmapped_sources


@pytest.mark.parametrize("fields,problem", [
    ({}, "missing_required"),                                                        # starts_at, venue
    ({"starts_at": "2026-11-01T18:00:00", "venue": {"city": "X"}}, "time zone"),
    ({"starts_at": "2026-11-01T18:00:00Z", "venue": {"address": "1 Road"}}, "missing city"),
    ({"starts_at": "2026-11-01T18:00:00Z", "venue": {"city": "X"}, "capacity": 0}, "at least 1"),
    ({"starts_at": "2026-11-01T18:00:00Z", "venue": {"city": "X"}, "is_online": "yes"}, "true/false"),
    ({"starts_at": "2026-11-01T18:00:00Z", "venue": "Kampala"}, "expects an object"),
])
def test_invalid_or_missing_values_are_refused(fields, problem):
    result = map_publication(_capability(EVENT_SCHEMA), _content(**fields))
    assert not result.ok
    text = json.dumps(result.report())
    assert problem in text if problem != "missing_required" else result.missing_required == ["starts_at", "venue"]


def test_enums_rich_text_media_and_ambiguity():
    schema = {"type": "object", "required": ["title", "slug", "body"], "properties": {
        "title": {"type": "string"}, "slug": {"type": "string"}, "body": {"type": "string", "format": "html"},
        "content": {"type": "string", "format": "html"}, "kind": {"type": "string", "enum": ["Guide", "News"]},
        "hero": {"type": "string", "format": "uri"}, "plain": {"type": "string"}}}
    result = map_publication(_capability(schema), _content(kind="guide"))
    assert not result.ok and "'body' could fill ['body', 'content']" in result.ambiguous[0]
    fixed = map_publication(_capability(schema, field_map={"body": "body"}), _content(kind="guide"))
    assert fixed.ok and fixed.payload["kind"] == "Guide" and "hero" not in fixed.payload   # media never invented
    plain = map_publication(_capability({"type": "object", "properties": {
        "name": {"type": "string"}, "slug": {"type": "string"}, "text": {"type": "string"}}}), _content())
    assert plain.payload["text"] == "Join us."              # plain-text field: HTML removed
    lost = map_publication(_capability({"type": "object", "properties": {"name": {"type": "string"},
                                                                         "slug": {"type": "string"}}}), _content())
    assert not lost.ok and "would be lost" in lost.errors[0]


def test_wordpress_tags_are_not_keywords():
    schema = {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"},
                                               "slug": {"type": "string"},
                                               "tags": {"type": "array", "items": {"type": "integer"}}}}
    result = map_publication(_capability(schema), _content())
    assert result.ok and "tags" not in result.payload and "meta_keywords" in result.unmapped_sources


# ── Framework adaptation: OpenAPI-described backends ─────────────────────────

LARAVEL_OPENAPI = {
    "openapi": "3.0.3", "info": {"title": "Shop API", "version": "1.4"},
    "components": {"schemas": {"PostInput": POST_SCHEMA,
                               "ProductInput": {"type": "object", "required": ["name", "slug", "price", "sku"],
                                                "properties": {"name": {"type": "string"},
                                                               "slug": {"type": "string"},
                                                               "description": {"type": "string", "format": "html"},
                                                               "price": {"type": "number", "minimum": 0},
                                                               "sku": {"type": "string"},
                                                               "category_id": {"type": "integer"}}}}},
    "paths": {
        "/posts": {"get": {}, "post": {"summary": "Blog posts", "x-tekjuice-content-type": {
            "name": "post", "public_url_template": "https://www.customer.example/blog/{slug}"},
            "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/PostInput"}}}}}},
        "/posts/{post}": {"get": {}, "put": {}, "delete": {}},
        "/posts/{post}/publish": {"post": {}},
        "/products": {"post": {"requestBody": {"content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/ProductInput"}}}}}},
        "/products/{product}": {"patch": {}},
        "/users/{user}": {"get": {}},
    }}
FASTAPI_OPENAPI = {
    "openapi": "3.1.0", "info": {"title": "Events service", "version": "0.3"},
    "paths": {"/v1/items/webinars/": {"post": {"x-tekjuice-content-type": True, "requestBody": {"content": {
        "application/json": {"schema": {"type": "object", "required": ["headline", "slug", "body_html"], "properties": {
            "headline": {"type": "string"}, "slug": {"type": "string"}, "body_html": {"type": "string"},
            "starts_at": {"type": ["string", "null"], "format": "date-time"}}}}}}}},
              "/v1/items/webinars/{slug}": {"patch": {}}}}


def test_two_backends_with_different_conventions_for_equivalent_types():
    laravel = {c.name: c for c in capabilities_from_openapi(LARAVEL_OPENAPI)}
    fastapi = {c.name: c for c in capabilities_from_openapi(FASTAPI_OPENAPI)}
    post, product, webinar = laravel["post"], laravel["product"], fastapi["webinar"]
    assert post.operations["update"].to_dict() == {"method": "PUT", "path": "/posts/{id}"} and post.identity == "id"
    assert "publish" in post.operations and post.authorized and post.category == "editorial"
    assert product.authorized is False and product.operations["update"].method == "PATCH"   # not enabled
    assert webinar.identity == "slug" and webinar.operations["update"].path == "/v1/items/webinars/{slug}"
    assert {f.name for f in webinar.fields} == {"headline", "slug", "body_html", "starts_at"}
    assert "users" not in laravel and "user" not in laravel          # no create operation: not a content type


def test_openapi_types_need_explicit_enablement(tmp_path):
    backend = FakeBackend(openapi=LARAVEL_OPENAPI, required={"products": ["sku"]})
    source = openapi_source(BASE, ledger_path=str(tmp_path / "l.db"), token=TOKEN, request=backend)
    reg = ContentTypeRegistry([source], default=None)
    assert reg.get("post").status == READY and reg.get("product").status == VALIDATED
    assert "not enabled" in reg.get("product").limitations[-1]
    enabled = ContentTypeRegistry([source], default=None, allow=["product"])
    assert enabled.get("product").status == READY and "TEKJUICE_CONTENT_TYPES" in enabled.get("product").evidence[-1]


def test_wordpress_types_from_rest(tmp_path):
    def wp(method, url, headers, body, timeout):
        assert headers["Authorization"].startswith("Basic ")
        if url.endswith("/wp/v2/types?context=edit"):
            return 200, {"post": {"name": "Posts", "rest_base": "posts", "rest_namespace": "wp/v2", "viewable": True},
                         "page": {"name": "Pages", "rest_base": "pages", "viewable": True},
                         "attachment": {"name": "Media", "rest_base": "media"},
                         "product": {"name": "Products", "rest_base": "products", "rest_namespace": "wc/v3"}}
        if method == "OPTIONS" and url.endswith("/wp/v2/posts"):
            return 200, {"schema": {"type": "object", "properties": {
                "title": {"type": "object", "properties": {"raw": {"type": "string"}, "rendered": {"type": "string"}}},
                "content": {"type": "object", "properties": {"raw": {"type": "string"}}},
                "slug": {"type": "string"}, "status": {"type": "string", "enum": ["publish", "draft", "pending"]},
                "tags": {"type": "array", "items": {"type": "integer"}}, "link": {"type": "string"}}}}
        return 404, None
    reg = ContentTypeRegistry([wordpress_source("https://blog.customer.example", username="tekjuice",
                                                application_password="abcd efgh", ledger_path=str(tmp_path / "l.db"),
                                                request=wp)], default=None, allow=["post"])
    assert reg.names() == ["page", "post", "product"]               # media is internal
    assert reg.get("post").status == READY and reg.get("page").status == UNSUPPORTED    # no schema served
    assert reg.get("product").status == UNSUPPORTED
    payload = map_publication(reg.get("post"), _content()).payload
    assert payload == {"title": "Launch party", "content": "<p>Join <b>us</b>.</p>", "slug": "launch-party"}


# ── Typed publication through the receiver ───────────────────────────────────

def _receiver(reg, store=None):
    return PublicationReceiver(store=store or InMemoryContentStore("https://www.customer.example"),
                               website_id=WEBSITE_ID, receiver_secret=RECEIVER_SECRET, content_types=reg)


def test_typed_content_is_routed_created_updated_and_replayed(tmp_path):
    backend = FakeBackend(manifest=manifest(EVENTS, ISSUES), required={"events": ["starts_at"]})
    store = InMemoryContentStore("https://www.customer.example")
    receiver = _receiver(registry(backend, tmp_path, store=store), store)
    assert "publish.content_types" in receiver.capabilities
    fields = {"starts_at": "2026-11-01T18:00:00Z", "venue": {"city": "Kampala"}}
    created = send(receiver, typed_payload("event", fields=fields))
    assert created.status_code == 200, created.body
    assert created.body["content_type"] == "event" and created.body["state"] == "published"
    assert created.body["published_url"] == "https://www.customer.example/events/launch-party"
    assert backend.records["events"] and not store.find_by_slug("launch-party")   # not in the article store
    calls = len(backend.calls)
    again = send(receiver, typed_payload("event", fields=fields))                     # retry: no duplicate
    assert again.body["replayed"] is True and len(backend.calls) == calls and len(backend.records["events"]) == 1
    updated = send(receiver, typed_payload("event", action="update", fields={**fields, "capacity": 80}, key="key-2",
                                           execution="exec-2"))
    assert updated.status_code == 200 and updated.body["action_performed"] == "updated"
    assert backend.calls[-1][0] == "PATCH" and backend.calls[-1][1].endswith("/events/launch-party")
    issue = send(receiver, typed_payload("newsletter_issue", slug="october-issue", key="key-3"))
    assert issue.body["state"] == "saved" and issue.body["public"] is False and issue.body["published_url"] == ""
    assert not any("/send" in c[1] for c in backend.calls)                             # never sent to subscribers


def test_untyped_publications_are_unchanged(tmp_path):
    store = InMemoryContentStore("https://www.customer.example")
    receiver = _receiver(registry(FakeBackend(manifest=manifest(EVENTS)), tmp_path, store=store), store)
    response = send(receiver, typed_payload(None))
    assert response.status_code == 200 and "content_type" not in response.body and "state" not in response.body
    assert store.find_by_slug("launch-party") is not None
    article = send(receiver, typed_payload("article", slug="named-article", key="key-9"))
    assert article.body["content_type"] == "article" and store.find_by_slug("named-article") is not None


@pytest.mark.parametrize("payload,code", [
    (typed_payload("product"), "content_type_unavailable"),                       # unknown type
    (typed_payload("newsletter_issue", action="update"), "content_type_unavailable"),   # operation not offered
    (typed_payload("event", fields={"venue": {"city": "X"}}), "content_mapping_failed"),  # starts_at missing
])
def test_wrong_or_unusable_destinations_fail_safely(tmp_path, payload, code):
    backend = FakeBackend(manifest=manifest(EVENTS, ISSUES))
    response = send(_receiver(registry(backend, tmp_path)), payload)
    assert response.status_code == 422 and response.body["error"]["code"] == code
    assert not any(c[0] in ("POST", "PUT", "PATCH") for c in backend.calls)    # nothing was written


def test_backend_errors_timeouts_and_rejections(tmp_path):
    fields = {"starts_at": "2026-11-01T18:00:00Z", "venue": {"city": "Kampala"}}
    backend = FakeBackend(manifest=manifest(EVENTS), required={"events": ["organiser"]})
    receiver = _receiver(registry(backend, tmp_path))
    rejected = send(receiver, typed_payload("event", fields=fields))
    assert rejected.status_code == 422 and "organiser: The organiser field is required." in rejected.body["error"]["message"]
    backend.required = {}
    backend.fail_with = 500
    failed = send(receiver, typed_payload("event", fields=fields, key="key-5"))
    assert failed.status_code == 503 and failed.body["error"]["code"] == "storage_unavailable"
    backend.fail_with = None
    backend.raise_error = TimeoutError("read timed out")
    timed_out = send(receiver, typed_payload("event", fields=fields, key="key-6"))
    assert timed_out.status_code == 503 and TOKEN not in json.dumps(timed_out.body)


def test_verify_dry_run_writes_nothing_and_reports_the_destination(tmp_path):
    backend = FakeBackend(manifest=manifest(EVENTS))
    receiver = _receiver(registry(backend, tmp_path))
    publication = typed_payload("event", fields={"starts_at": "2026-11-01T18:00:00Z", "venue": {"city": "X"}})
    response = send(receiver, {"contract_version": "1.0", "action": "verify", "website_id": WEBSITE_ID,
                               "challenge": "c-1", "dry_run": True, "publication": publication})
    assert response.status_code == 200 and response.body["content_type"] == "event"
    assert response.body["would_publish_url"] == "https://www.customer.example/events/launch-party"
    assert not any(c[0] in ("POST", "PUT", "PATCH") for c in backend.calls)


def test_capability_map_is_served_without_secrets(tmp_path):
    receiver = _receiver(registry(FakeBackend(manifest=manifest(EVENTS, ISSUES)), tmp_path))
    response = send(receiver, {"contract_version": "1.0", "action": "capabilities", "website_id": WEBSITE_ID})
    described = response.body["content_types"]
    assert described["default"] == "article" and described["ready"] == ["article", "event", "newsletter_issue"]
    text = json.dumps(described)
    assert TOKEN not in text and "backend:3000" not in text
    event = next(t for t in described["types"] if t["name"] == "event")
    assert event["required_fields"] == ["name", "slug", "starts_at", "venue"] and event["status"] == "ready"


def test_ledgers_are_scoped_per_content_type(tmp_path):
    fields = {"starts_at": "2026-11-01T18:00:00Z", "venue": {"city": "X"}}
    workshop = dict(EVENTS, name="workshop", operations={"create": {"path": "/workshops"}})
    backend = FakeBackend(manifest=manifest(EVENTS, workshop))
    receiver = _receiver(registry(backend, tmp_path))
    assert send(receiver, typed_payload("event", fields=fields)).status_code == 200
    other = send(receiver, typed_payload("workshop", fields=fields, key="key-w", execution="exec-w"))
    assert other.status_code == 200 and other.body["replayed"] is False     # same slug, other type: its own record
    assert len(backend.records["events"]) == 1 and len(backend.records["workshops"]) == 1


def test_a_receiver_without_content_types_refuses_typed_content():
    receiver = PublicationReceiver(store=InMemoryContentStore("https://www.customer.example"), website_id=WEBSITE_ID,
                                   receiver_secret=RECEIVER_SECRET)
    assert "publish.content_types" not in receiver.capabilities
    response = send(receiver, typed_payload("event", fields={}))
    assert response.status_code == 422 and response.body["error"]["code"] == "content_type_unavailable"


def test_diagnostic_report_shows_status_and_gaps_without_secrets(tmp_path):
    from tekjuice_connector.cli.content_types import render

    described = registry(FakeBackend(manifest=manifest(EVENTS, dict(EVENTS, name="faq", schema=None))),
                         tmp_path).describe()
    text = render(described)
    assert "event (Events) — READY" in text and "faq (Events) — UNSUPPORTED" in text
    assert "! the backend did not describe this type's fields" in text
    assert "Tek Juice performs: create, update" in text and TOKEN not in text and "backend:3000" not in text
