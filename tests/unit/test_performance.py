"""performance.*: code-free, reversible delivery settings applied by middleware."""

import asyncio
import gzip
import json

import pytest

from tekjuice_connector.publication import InMemoryContentStore, PublicationReceiver
from tekjuice_connector.publication.errors import ContractViolationError
from tekjuice_connector.publication.performance import (
    PerformanceControls,
    cache_control_for,
    is_versioned_asset,
    parse_performance_change,
)

SECRET = "s" * 40
CACHE = {"type": "static_cache", "enabled": True, "max_age": 31536000, "path_prefixes": ["/assets/"]}
GZIP = {"type": "compression", "enabled": True, "min_bytes": 256}
JS = b"function hello(){return 'hello world';}\n" * 50


def _receiver(controls=None):
    store = InMemoryContentStore("https://site.example")
    return PublicationReceiver(store=store, website_id="w", receiver_secret=SECRET, performance=controls)


def _post(receiver, payload, secret=SECRET, version="1.0"):
    body = json.dumps({"contract_version": version, "website_id": "w", **payload}).encode()
    return receiver.handle({"authorization": f"Bearer {secret}", "x-tekjuice-contract-version": version}, body)


def _change(receiver, change, key="perf-change-1"):
    return _post(receiver, {"action": "performance_change", "idempotency_key": key, "change": change})


# ── ASGI test double ───────────────────────────────────────────────────────

def _asgi_app(body=JS, content_type="application/javascript", status=200, extra=()):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", content_type.encode()), (b"etag", b'"abc"'), *extra]})
        await send({"type": "http.response.body", "body": body})
    return app


def _call(app, path, *, method="GET", headers=()):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path,
             "headers": [(k.encode(), v.encode()) for k, v in headers]}
    asyncio.run(app(scope, receive, send))
    start = sent[0]
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, b"".join(
        m.get("body", b"") for m in sent[1:])


# ── Capability discovery ──────────────────────────────────────────────────

def test_nothing_is_advertised_until_the_middleware_is_installed():
    controls = PerformanceControls()
    receiver = _receiver(controls)
    assert not any(c.startswith("performance.") for c in receiver.capabilities)
    found = _post(receiver, {"action": "capabilities"})
    assert found.status_code == 200
    assert found.body["capability_details"] == {}
    assert "performance.static_cache" in found.body["unsupported"]
    # A change is refused with a structured unsupported result.
    refused = _change(receiver, CACHE)
    assert refused.status_code == 422
    assert refused.body["error"]["code"] == "unsupported_capability"
    assert refused.body["error"]["capability"] == "performance.static_cache"


def test_discovery_is_versioned_and_reports_real_scope_only():
    controls = PerformanceControls()
    controls.asgi(_asgi_app())
    receiver = _receiver(controls)
    body = _post(receiver, {"action": "capabilities"}).body
    assert body["discovery_version"] == "1" and body["contract_versions"] == ["1.0"]
    assert {"performance.static_cache", "performance.compression", "performance.snapshot"} <= set(body["capabilities"])
    assert body["capability_details"]["performance.static_cache"]["version"] == "1"
    assert "gzip" in body["capability_details"]["performance.compression"]["encodings"]
    # Image and CDN work has no implementation: never advertised.
    assert "performance.image_delivery" not in body["capabilities"]
    assert "performance.cdn" in body["unsupported"]


def test_discovery_requires_authentication_and_the_right_website_and_contract():
    controls = PerformanceControls()
    controls.asgi(_asgi_app())
    receiver = _receiver(controls)
    assert _post(receiver, {"action": "capabilities"}, secret="x" * 40).status_code == 401
    other = json.dumps({"contract_version": "1.0", "website_id": "other", "action": "capabilities"}).encode()
    assert receiver.handle({"authorization": f"Bearer {SECRET}"}, other).status_code == 403
    assert _post(receiver, {"action": "capabilities"}, version="9.0").status_code == 422


def test_plain_receiver_has_no_performance_capabilities():
    receiver = _receiver(None)
    assert _post(receiver, {"action": "capabilities"}).body["capability_details"] == {}
    assert _change(receiver, GZIP).body["error"]["code"] == "unsupported_capability"


def test_image_and_cdn_changes_are_structured_unsupported_results():
    controls = PerformanceControls()
    controls.asgi(_asgi_app())
    receiver = _receiver(controls)
    for kind, capability in (("image_delivery", "performance.image_delivery"), ("cdn", "performance.cdn")):
        response = _change(receiver, {"type": kind})
        assert response.status_code == 422
        assert response.body["error"] == {**response.body["error"], "code": "unsupported_capability",
                                          "capability": capability}


# ── Validation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("change", [
    {**CACHE, "path_prefixes": ["/"]},                 # the whole site
    {**CACHE, "path_prefixes": ["/../etc/"]},
    {**CACHE, "path_prefixes": []},
    {**CACHE, "max_age": 10},
    {**CACHE, "max_age": 10 ** 9},
    {**GZIP, "min_bytes": 1},
    {**CACHE, "headers": {"x": "y"}},                  # no arbitrary headers
    {"type": "rewrite_html"},
])
def test_unsafe_or_unknown_changes_are_rejected(change):
    with pytest.raises(ContractViolationError):
        parse_performance_change({"contract_version": "1.0", "action": "performance_change", "website_id": "w",
                                  "idempotency_key": "perf-key-1", "change": change})


@pytest.mark.parametrize("path,versioned", [
    ("/assets/index-BxY1z2Ab.js", True),
    ("/assets/main.3f9a1c2b.css", True),
    ("/assets/index-DqWkTzRa.js", True),
    ("/assets/app.js", False),
    ("/assets/logo.png", False),
    ("/assets/MyComponent-Component.js", False),
    ("/assets/vendor-polyfill.js", False),
    ("/index.html", False),
])
def test_only_content_hashed_files_count_as_versioned(path, versioned):
    assert is_versioned_asset(path) is versioned


# ── Cache safety ──────────────────────────────────────────────────────────

CONFIG = {"static_cache": {"enabled": True, "max_age": 31536000, "path_prefixes": ["/assets/"]}, "compression": None}


@pytest.mark.parametrize("kwargs", [
    {"path": "/assets/index.html", "response_headers": [("Content-Type", "text/html")]},
    {"path": "/assets/data-1a2b3c4d.json", "response_headers": [("Content-Type", "application/json")]},
    {"path": "/assets/app.js", "response_headers": [("Content-Type", "application/javascript")]},
    {"path": "/api/index-1a2b3c4d.js", "response_headers": [("Content-Type", "application/javascript")]},
    {"path": "/assets/index-1a2b3c4d.js", "response_headers": [("Set-Cookie", "session=1")]},
    {"path": "/assets/index-1a2b3c4d.js", "response_headers": [("Cache-Control", "private, max-age=60")]},
    {"path": "/assets/index-1a2b3c4d.js", "response_headers": [("Cache-Control", "no-store")]},
    {"path": "/assets/index-1a2b3c4d.js", "request_headers": [("authorization", "Bearer t")]},
    {"path": "/assets/index-1a2b3c4d.js", "status": 404},
    {"path": "/assets/index-1a2b3c4d.js", "method": "POST"},
])
def test_sensitive_or_unversioned_responses_are_never_cached_publicly(kwargs):
    args = {"method": "GET", "status": 200, "request_headers": [], "response_headers": [], **kwargs}
    assert cache_control_for(CONFIG, **args) is None


def test_versioned_asset_gets_long_lived_cache():
    assert cache_control_for(CONFIG, method="GET", path="/assets/index-1a2b3c4d.js", status=200, request_headers=[],
                             response_headers=[("Content-Type", "application/javascript"),
                                               ("Cache-Control", "no-cache")]) == "public, max-age=31536000, immutable"


# ── Middleware end to end, snapshot and rollback ──────────────────────────

def test_change_applies_through_asgi_middleware_and_restore_undoes_it():
    controls = PerformanceControls()
    app = controls.asgi(_asgi_app())
    receiver = _receiver(controls)
    path = "/assets/index-1a2b3c4d.js"
    _, before, _ = _call(app, path)
    assert "cache-control" not in before
    assert path.rsplit("/", 1)[0] + "/" in _post(receiver, {"action": "capabilities"}).body[
        "capability_details"]["performance.static_cache"]["observed_static_prefixes"]

    applied = _change(receiver, CACHE)
    assert applied.status_code == 200 and applied.body["status"] == "applied"
    assert applied.body["previous"] == {"static_cache": None, "compression": None}
    _, after, body = _call(app, path)
    assert after["cache-control"] == "public, max-age=31536000, immutable" and body == JS
    # The HTML shell and the API stay as they were.
    _, html, _ = _call(controls.asgi(_asgi_app(b"<html></html>", "text/html")), "/")
    assert "cache-control" not in html

    restored = _change(receiver, {"type": "restore", "snapshot": applied.body["previous"]}, key="perf-restore-1")
    assert restored.body["current"] == {"static_cache": None, "compression": None}
    _, reverted, _ = _call(app, path)
    assert "cache-control" not in reverted


def test_repeated_idempotency_key_replays_without_reapplying():
    controls = PerformanceControls()
    controls.asgi(_asgi_app())
    receiver = _receiver(controls)
    first = _change(receiver, CACHE)
    second = _change(receiver, CACHE)
    assert second.body["replayed"] is True
    assert second.body["config_version"] == first.body["config_version"]
    assert second.body["previous"] == first.body["previous"]


def test_compression_negotiates_and_preserves_content():
    controls = PerformanceControls()
    app = controls.asgi(_asgi_app())
    receiver = _receiver(controls)
    assert _change(receiver, GZIP).status_code == 200
    _, headers, body = _call(app, "/assets/app.js", headers=[("accept-encoding", "gzip, deflate")])
    assert headers["content-encoding"] == "gzip" and gzip.decompress(body) == JS
    assert headers["vary"] == "Accept-Encoding" and headers["etag"] == 'W/"abc"'
    assert int(headers["content-length"]) == len(body)
    # No Accept-Encoding, q=0, already encoded, small or streaming: unchanged.
    for request, response_app in (([], _asgi_app()), ([("accept-encoding", "gzip;q=0")], _asgi_app()),
                                  ([("accept-encoding", "gzip")], _asgi_app(extra=[(b"content-encoding", b"br")])),
                                  ([("accept-encoding", "gzip")], _asgi_app(b"tiny")),
                                  ([("accept-encoding", "gzip")], _asgi_app(JS, "text/event-stream")),
                                  ([("accept-encoding", "gzip")], _asgi_app(JS, "image/png"))):
        _, plain, raw = _call(controls.asgi(response_app), "/assets/app.js", headers=request)
        assert "content-encoding" not in plain or plain["content-encoding"] == "br"
        assert raw in (JS, b"tiny")


def test_wsgi_middleware_applies_the_same_rules():
    controls = PerformanceControls()

    def wsgi_app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/css"), ("Content-Length", str(len(JS)))])
        return [JS]

    app = controls.wsgi(wsgi_app)
    receiver = _receiver(controls)
    _change(receiver, CACHE)
    _change(receiver, GZIP, key="perf-change-2")
    captured = {}
    body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": "/assets/site-9f8e7d6c.css",
                         "HTTP_ACCEPT_ENCODING": "gzip"},
                        lambda status, headers, exc=None: captured.update(status=status, headers=dict(headers))))
    assert captured["headers"]["Cache-Control"] == "public, max-age=31536000, immutable"
    assert captured["headers"]["Content-Encoding"] == "gzip" and gzip.decompress(body) == JS
    assert captured["headers"]["Content-Length"] == str(len(body))


def test_configuration_persists_across_processes(tmp_path):
    path = str(tmp_path / "performance.json")
    controls = PerformanceControls(path)
    controls.asgi(_asgi_app())
    _change(_receiver(controls), CACHE)
    reloaded = PerformanceControls(path)
    assert reloaded.config()["static_cache"]["path_prefixes"] == ["/assets/"]
    assert oct((tmp_path / "performance.json").stat().st_mode & 0o777) == "0o600"


def test_existing_publication_and_site_change_actions_are_unchanged():
    controls = PerformanceControls()
    controls.asgi(_asgi_app())
    receiver = _receiver(controls)
    assert {"publish.create", "publish.update"} <= set(receiver.capabilities)
    # site.changes still needs SeoPages; adding performance controls doesn't grant it.
    assert "site.changes" not in receiver.capabilities
