"""Content types against real backends over real HTTP.

Two Node.js processes with different conventions for equivalent content
(tests/fixtures/backends/rest_backend.js): a Laravel-style API described by
OpenAPI (numeric ids, PUT, {"data": ...} responses, Laravel 422 errors) and
a content API manifest (slug identity, PATCH, a newsletter type with no
public page and a send action). The receiver discovers both, routes each
publication to the right resource and never performs an operation the
publication did not ask for. Skipped without Node.js.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import requests

from tekjuice_connector.publication import (
    InMemoryContentStore,
    PublicationReceiver,
    content_api_source,
    native_source,
    openapi_source,
)
from tekjuice_connector.publication.content_types import ContentTypeRegistry
from tests.fixtures.publication_fixtures import AUTH_HEADERS, RECEIVER_SECRET, WEBSITE_ID
from tests.unit.test_content_types import typed_payload

BACKEND = Path(__file__).resolve().parents[1] / "fixtures" / "backends" / "rest_backend.js"
TOKEN = "backend-token-" + "b" * 32

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


@pytest.fixture
def backends():
    started = {}
    for style in ("laravel", "manifest"):
        process = subprocess.Popen(["node", str(BACKEND)], stdout=subprocess.PIPE, text=True,
                                   env={"STYLE": style, "TOKEN": TOKEN, "PORT": "0", "PATH": str(Path(shutil.which("node")).parent)})
        line = process.stdout.readline().strip()
        assert line.startswith("listening "), line
        prefix = "/api" if style == "laravel" else "/v1"
        started[style] = (process, f"http://127.0.0.1:{line.split()[1]}{prefix}")
    yield {k: v[1] for k, v in started.items()}
    for process, _ in started.values():
        process.terminate()
        process.wait(timeout=5)


def _records(base):
    return requests.get(base + "/_records", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5).json()


def _receiver(backends, tmp_path, allow=()):
    store = InMemoryContentStore("https://www.customer.example")
    ledger = str(tmp_path / "content-types.sqlite3")
    registry = ContentTypeRegistry([
        native_source(store),
        openapi_source(backends["laravel"], document_path="/openapi.json", ledger_path=ledger, token=TOKEN),
        content_api_source(backends["manifest"], ledger_path=ledger, token=TOKEN),
    ], allow=allow)
    return PublicationReceiver(store=store, website_id=WEBSITE_ID, receiver_secret=RECEIVER_SECRET,
                               content_types=registry), store


def send(receiver, payload):
    return receiver.handle(AUTH_HEADERS, json.dumps(payload).encode())


def test_discovery_across_two_real_backends(backends, tmp_path):
    receiver, _ = _receiver(backends, tmp_path)
    described = send(receiver, {"contract_version": "1.0", "action": "capabilities",
                                "website_id": WEBSITE_ID}).body["content_types"]
    status = {t["name"]: t["status"] for t in described["types"]}
    assert status == {"article": "ready", "post": "ready", "product": "validated", "event": "ready",
                      "newsletter_issue": "ready"}, described
    by_name = {t["name"]: t for t in described["types"]}
    assert by_name["post"]["identity"] == "id" and by_name["event"]["identity"] == "slug"
    assert by_name["newsletter_issue"]["executable_operations"] == ["create"]
    assert TOKEN not in json.dumps(described) and "127.0.0.1" not in json.dumps(described)


def test_publications_reach_the_right_resource_on_each_backend(backends, tmp_path):
    receiver, store = _receiver(backends, tmp_path)
    post = send(receiver, typed_payload("post", slug="spring-update", key="k-post"))
    assert post.status_code == 200, post.body
    assert post.body["published_url"] == "https://laravel.customer.example/posts/spring-update"
    event = send(receiver, typed_payload("event", slug="spring-fair", key="k-event",
                                         fields={"starts_at": "2026-04-01T10:00:00+01:00", "capacity": 50}))
    assert event.status_code == 200, event.body
    assert event.body["published_url"] == "https://manifest.customer.example/events/spring-fair"
    issue = send(receiver, typed_payload("newsletter_issue", slug="april-issue", key="k-issue"))
    assert issue.body["state"] == "saved" and issue.body["published_url"] == ""

    laravel, manifest = _records(backends["laravel"]), _records(backends["manifest"])
    assert [r["slug"] for r in laravel["posts"]] == ["spring-update"] and "products" not in laravel
    assert laravel["posts"][0]["body"].startswith("<p>")             # body -> body (same name)
    assert [r["slug"] for r in manifest["events"]] == ["spring-fair"]
    assert manifest["events"][0]["name"] == "Launch party" and manifest["events"][0]["description"].startswith("<p>")
    assert manifest["issues"][0]["subject"] == "Launch party" and manifest["issues"][0]["html"].startswith("<p>")
    assert store.find_by_slug("spring-update") is None                  # not in the article store

    # Retries create nothing new; updates use each backend's own method and identity.
    assert send(receiver, typed_payload("post", slug="spring-update", key="k-post")).body["replayed"] is True
    assert len(_records(backends["laravel"])["posts"]) == 1
    updated = send(receiver, typed_payload("post", action="update", slug="spring-update", key="k-post-2",
                                           execution="exec-2", title="Spring update (revised)"))
    assert updated.status_code == 200 and _records(backends["laravel"])["posts"][0]["title"] == "Spring update (revised)"
    moved = send(receiver, typed_payload("event", action="update", slug="spring-fair", key="k-event-2",
                                         execution="exec-3", fields={"starts_at": "2026-04-02T10:00:00+01:00"}))
    assert moved.status_code == 200 and _records(backends["manifest"])["events"][0]["starts_at"].startswith("2026-04-02")


def test_unsafe_requests_are_refused_before_reaching_a_backend(backends, tmp_path):
    receiver, _ = _receiver(backends, tmp_path)
    product = send(receiver, typed_payload("product", slug="mug", key="k-mug", fields={"price": 9.5}))
    assert product.status_code == 422 and product.body["error"]["code"] == "content_type_unavailable"
    assert any("not enabled" in r for r in product.body["error"]["reasons"])
    no_date = send(receiver, typed_payload("event", slug="no-date", key="k-nodate"))
    assert no_date.status_code == 422 and no_date.body["error"]["mapping"]["missing_required"] == ["starts_at"]
    too_long = send(receiver, typed_payload("post", slug="long", key="k-long", title="x" * 200))
    assert too_long.status_code == 422 and "at most 120" in too_long.body["error"]["message"]
    assert "products" not in _records(backends["laravel"]) and "posts" not in _records(backends["laravel"])
    assert "events" not in _records(backends["manifest"])
    # Enabled by the owner, the product is publishable; the backend's own rules still apply.
    enabled, _ = _receiver(backends, tmp_path, allow=["product"])
    mug = send(enabled, typed_payload("product", slug="mug", key="k-mug-2", fields={"price": 9.5}))
    assert mug.status_code == 200 and _records(backends["laravel"])["products"][0]["price"] == 9.5


def test_a_wrong_credential_is_reported_not_retried_silently(backends, tmp_path):
    ledger = str(tmp_path / "l.sqlite3")
    registry = ContentTypeRegistry([content_api_source(backends["manifest"], ledger_path=ledger,
                                                       token="wrong-" + "w" * 40)], default=None)
    assert registry.describe()["types"] == [] and "refused the credential" in registry.describe()["errors"][0]
