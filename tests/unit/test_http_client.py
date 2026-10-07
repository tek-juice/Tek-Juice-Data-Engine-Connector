"""Unit tests — HTTP client layer."""

from __future__ import annotations

import pytest

from tekjuice_connector.client.headers import build_request_headers, safe_headers_for_logging
from tekjuice_connector.client.http import HttpResponse, HttpTransport
from tekjuice_connector.client.timeouts import TimeoutConfig
from tekjuice_connector.exceptions.api import MalformedResponseError, ServerError
from tekjuice_connector.exceptions.authentication import InvalidCredentialsError
from tekjuice_connector.exceptions.connection import ConnectionTimeoutError
from tekjuice_connector.exceptions.connection import (
    ConnectionError as ConnectorConnectionError,
)
from tekjuice_connector.models.common import CONNECTOR_KEY_HEADER
from tekjuice_connector.security.redaction import REDACTED_MARKER
from tests.fixtures.transport_fixtures import FakeTransport


# ---------------------------------------------------------------------------
# TimeoutConfig
# ---------------------------------------------------------------------------

class TestTimeoutConfig:
    def test_defaults(self):
        tc = TimeoutConfig()
        assert tc.connect == 10.0
        assert tc.read == 30.0

    def test_as_tuple(self):
        tc = TimeoutConfig(connect=5.0, read=15.0)
        assert tc.as_tuple() == (5.0, 15.0)

    def test_repr(self):
        assert "TimeoutConfig" in repr(TimeoutConfig())


# ---------------------------------------------------------------------------
# build_request_headers / safe_headers_for_logging
# ---------------------------------------------------------------------------

class TestHeaders:
    def test_connector_key_present(self):
        h = build_request_headers("my-key")
        assert h[CONNECTOR_KEY_HEADER] == "my-key"

    def test_content_type_json(self):
        h = build_request_headers("k")
        assert h["Content-Type"] == "application/json"

    def test_accept_json(self):
        h = build_request_headers("k")
        assert h["Accept"] == "application/json"

    def test_user_agent_present(self):
        h = build_request_headers("k")
        assert "tekjuice-connector" in h["User-Agent"]

    def test_safe_headers_redacts_key(self):
        h = build_request_headers("super-secret")
        safe = safe_headers_for_logging(h)
        assert safe[CONNECTOR_KEY_HEADER] == REDACTED_MARKER
        assert "super-secret" not in str(safe)


# ---------------------------------------------------------------------------
# HttpResponse
# ---------------------------------------------------------------------------

class TestHttpResponse:
    def test_ok_true_for_200(self):
        r = HttpResponse(status_code=200, body={"ok": True})
        assert r.ok is True

    def test_ok_false_for_400(self):
        r = HttpResponse(status_code=400, body={})
        assert r.ok is False

    def test_ok_false_for_500(self):
        r = HttpResponse(status_code=500, body={})
        assert r.ok is False

    def test_repr_shows_status(self):
        r = HttpResponse(status_code=200, body={})
        assert "200" in repr(r)


# ---------------------------------------------------------------------------
# FakeTransport behaviour
# ---------------------------------------------------------------------------

class TestFakeTransport:
    def test_returns_configured_response(self):
        body = {"website_id": "x", "ready": True}
        t = FakeTransport(responses=[(200, body)])
        resp = t.post("/test", connector_key="k", payload={})
        assert resp.status_code == 200
        assert resp.body == body

    def test_raises_on_401(self):
        t = FakeTransport(responses=[(401, {})])
        with pytest.raises(InvalidCredentialsError):
            t.post("/test", connector_key="k", payload={})

    def test_raises_on_403(self):
        t = FakeTransport(responses=[(403, {})])
        with pytest.raises(InvalidCredentialsError):
            t.post("/test", connector_key="k", payload={})

    def test_raises_on_500(self):
        t = FakeTransport(responses=[(500, {})])
        with pytest.raises(ServerError):
            t.post("/test", connector_key="k", payload={})

    def test_raise_on_call_exception(self):
        exc = ConnectionTimeoutError(url="https://x.test", timeout=10.0)
        t = FakeTransport(raise_on_call=exc)
        with pytest.raises(ConnectionTimeoutError):
            t.post("/test", connector_key="k", payload={})

    def test_call_count_increments(self):
        t = FakeTransport(responses=[(200, {})])
        t.post("/test", connector_key="k", payload={})
        t.post("/test", connector_key="k", payload={})
        assert t.call_count == 2

    def test_last_path_recorded(self):
        t = FakeTransport(responses=[(200, {})])
        t.post("/api/v1/connector/handshake", connector_key="k", payload={})
        assert t.last_path == "/api/v1/connector/handshake"

    def test_last_payload_recorded(self):
        t = FakeTransport(responses=[(200, {})])
        t.post("/test", connector_key="k", payload={"website_id": "site-1"})
        assert t.last_payload == {"website_id": "site-1"}

    def test_non_dict_body_returned_as_is(self):
        t = FakeTransport(responses=[(200, "not-a-dict")])
        resp = t.post("/test", connector_key="k", payload={})
        assert resp.body == "not-a-dict"
