"""Unit tests — typed models."""

from __future__ import annotations

import pytest

from tekjuice_connector.models.authentication import HandshakeRequest, HandshakeResponse
from tekjuice_connector.models.connection import ConnectionResult, ConnectionState
from tekjuice_connector.models.credentials import ConnectorCredentials
from tekjuice_connector.models.errors import HttpErrorDetail, ValidationErrorDetail
from tests.fixtures.handshake_fixtures import (
    FAKE_WEBSITE_ID,
    HANDSHAKE_RESPONSE_READY,
    HANDSHAKE_RESPONSE_MISSING_READY,
)


class TestHandshakeRequest:
    def test_to_dict(self):
        req = HandshakeRequest(website_id="site-1")
        assert req.to_dict() == {"website_id": "site-1"}


class TestHandshakeResponse:
    def test_from_dict_success(self):
        resp = HandshakeResponse.from_dict(HANDSHAKE_RESPONSE_READY)
        assert resp.website_id == FAKE_WEBSITE_ID
        assert resp.ready is True
        assert resp.connector_authenticated is True

    def test_from_dict_missing_key_raises(self):
        with pytest.raises(KeyError):
            HandshakeResponse.from_dict(HANDSHAKE_RESPONSE_MISSING_READY)


class TestConnectionResult:
    def test_from_handshake_ready(self):
        resp = HandshakeResponse.from_dict(HANDSHAKE_RESPONSE_READY)
        result = ConnectionResult.from_handshake(resp)
        assert result.ready is True
        assert result.state is ConnectionState.READY

    def test_failed_factory(self):
        result = ConnectionResult.failed("something went wrong")
        assert result.ready is False
        assert result.state is ConnectionState.FAILED

    def test_repr_safe(self):
        resp = HandshakeResponse.from_dict(HANDSHAKE_RESPONSE_READY)
        result = ConnectionResult.from_handshake(resp)
        r = repr(result)
        assert "READY" in r
        assert "connector_key" not in r.lower()


class TestHttpErrorDetail:
    def test_str_representation(self):
        detail = HttpErrorDetail(
            status_code=500,
            endpoint="/api/v1/connector/handshake",
            message="Internal server error",
        )
        s = str(detail)
        assert "500" in s
        assert "/api/v1/connector/handshake" in s


class TestValidationErrorDetail:
    def test_str_with_hint(self):
        d = ValidationErrorDetail(field="website_id", issue="Empty", value_hint="(empty)")
        assert "website_id" in str(d)
        assert "Empty" in str(d)

    def test_str_without_hint(self):
        d = ValidationErrorDetail(field="connector_key", issue="Missing")
        assert "connector_key" in str(d)
