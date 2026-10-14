"""Contract tests — the same receiver behaves identically under every adapter.

Each adapter is exercised through its framework's real request handling
(WSGI environ, ASGI scope, FastAPI TestClient, Flask test client, Django
RequestFactory), not by calling the receiver directly.
"""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from tekjuice_connector.adapters.asgi import make_asgi_app
from tekjuice_connector.adapters.detect import detect_framework
from tekjuice_connector.adapters.wsgi import make_wsgi_app
from tekjuice_connector.publication import InMemoryContentStore, PublicationReceiver
from tests.fixtures.publication_fixtures import (
    AUTH_HEADERS,
    PUBLIC_BASE_URL,
    RECEIVER_SECRET,
    WEBSITE_ID,
    encode,
    publish_payload,
    verify_payload,
)


def _receiver(store=None):
    return PublicationReceiver(
        store=store or InMemoryContentStore(PUBLIC_BASE_URL),
        website_id=WEBSITE_ID,
        receiver_secret=RECEIVER_SECRET,
    )


# ── Adapter drivers: each returns (status_code, json_body) ───────────────────

def _call_wsgi(receiver, payload, headers, method="POST"):
    body = encode(payload)
    environ = {
        "REQUEST_METHOD": method,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": io.BytesIO(body),
    }
    for key, value in headers.items():
        if key.lower() != "content-type":
            environ["HTTP_" + key.upper().replace("-", "_")] = value
    captured = {}

    def start_response(status, response_headers):
        captured["status"] = int(status.split()[0])

    chunks = make_wsgi_app(receiver)(environ, start_response)
    raw = b"".join(chunks)
    return captured["status"], (json.loads(raw) if raw else None)


def _call_asgi(receiver, payload, headers, method="POST"):
    body = encode(payload)
    scope = {
        "type": "http",
        "method": method,
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(make_asgi_app(receiver)(scope, receive, send))
    status = sent[0]["status"]
    raw = sent[1]["body"]
    return status, (json.loads(raw) if raw else None)


def _call_fastapi(receiver, payload, headers, method="POST"):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tekjuice_connector.adapters.fastapi import create_router

    app = fastapi.FastAPI()
    app.include_router(create_router(receiver, path="/tekjuice/publish"))
    response = TestClient(app).request(
        method, "/tekjuice/publish", content=encode(payload), headers=headers
    )
    return response.status_code, (response.json() if response.content else None)


def _call_flask(receiver, payload, headers, method="POST"):
    flask = pytest.importorskip("flask")
    from tekjuice_connector.adapters.flask import create_blueprint

    app = flask.Flask("tekjuice-adapter-test")
    app.register_blueprint(create_blueprint(receiver, path="/tekjuice/publish"))
    response = app.test_client().open(
        "/tekjuice/publish", method=method, data=encode(payload), headers=headers
    )
    return response.status_code, response.get_json(silent=True)


def _call_django(receiver, payload, headers, method="POST"):
    pytest.importorskip("django")
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DEBUG=False, ALLOWED_HOSTS=["testserver"], SECRET_KEY="adapter-test"
        )
        import django

        django.setup()
    from django.test import RequestFactory

    from tekjuice_connector.adapters.django import create_view

    meta = {
        "HTTP_" + k.upper().replace("-", "_"): v
        for k, v in headers.items()
        if k.lower() != "content-type"
    }
    request = RequestFactory().generic(
        method, "/tekjuice/publish", data=encode(payload),
        content_type="application/json", **meta,
    )
    response = create_view(receiver)(request)
    raw = response.content
    return response.status_code, (json.loads(raw) if raw else None)


ADAPTERS = {
    "wsgi": _call_wsgi,
    "asgi": _call_asgi,
    "fastapi": _call_fastapi,
    "flask": _call_flask,
    "django": _call_django,
}


@pytest.fixture(params=sorted(ADAPTERS))
def call(request):
    return ADAPTERS[request.param]


# ── Identical contract behaviour across adapters ─────────────────────────────

def test_create_then_replay(call):
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    receiver = _receiver(store)

    status, body = call(receiver, publish_payload(), AUTH_HEADERS)
    assert status == 200
    assert body["action_performed"] == "created"
    assert body["published_url"] == f"{PUBLIC_BASE_URL}/moving-cost-guide"
    assert store.get_content("moving-cost-guide") is not None

    status, body = call(receiver, publish_payload(), AUTH_HEADERS)
    assert status == 200
    assert body["replayed"] is True


def test_verify_round_trip(call):
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    status, body = call(_receiver(store), verify_payload(challenge="c-1"), AUTH_HEADERS)

    assert status == 200
    assert body["status"] == "verified"
    assert body["challenge"] == "c-1"
    assert body["persisted"] is False
    assert store.find_by_slug("tekjuice-verification-0a1b2c") is None


@pytest.mark.parametrize(
    ("payload_factory", "headers", "expected"),
    [
        (publish_payload, {"Content-Type": "application/json"}, 401),
        (lambda: publish_payload(action="update", slug="missing"), AUTH_HEADERS, 404),
        (lambda: publish_payload(website_id="other-site"), AUTH_HEADERS, 403),
    ],
)
def test_error_statuses(call, payload_factory, headers, expected):
    status, body = call(_receiver(), payload_factory(), headers)
    assert status == expected
    assert body["status"] == "error"


def test_duplicate_slug_conflict(call):
    receiver = _receiver()
    call(receiver, publish_payload(slug="introduction"), AUTH_HEADERS)

    status, body = call(
        receiver,
        publish_payload(slug="introduction", execution_id="execution-2"),
        AUTH_HEADERS,
    )
    assert status == 409
    assert body["error"]["code"] == "duplicate_slug"


def test_unsupported_capability_fails_clearly(call):
    """A store without update support must fail loudly, not drop content."""
    store = InMemoryContentStore(PUBLIC_BASE_URL, supports_update=False)
    receiver = _receiver(store)
    call(receiver, publish_payload(), AUTH_HEADERS)

    status, body = call(
        receiver,
        publish_payload(action="update", execution_id="execution-2", title="New"),
        AUTH_HEADERS,
    )

    assert status == 422
    assert body["error"]["code"] == "unsupported_operation"
    assert store.get_content("moving-cost-guide").title == "Moving Cost Guide"

    status, body = call(receiver, verify_payload(), AUTH_HEADERS)
    assert body["capabilities"] == ["publish.create", "publish.discovery", "publish.verify"]


def test_non_post_methods_are_rejected(call):
    status, _ = call(_receiver(), {}, AUTH_HEADERS, method="GET")
    assert status == 405


def test_detect_framework_reports_an_imported_framework():
    pytest.importorskip("flask")
    assert detect_framework() in {"fastapi", "starlette", "django", "flask"}
