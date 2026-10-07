"""Contract tests — Data Engine handshake request/response contract.

These tests verify that the Connector sends exactly what the Data Engine
expects and correctly handles all documented response shapes.

The confirmed contract:

  POST /api/v1/connector/handshake
  Header: X-Tek-Juice-Connector-Key: <connector_key>
  Body:   {"website_id": "<website_id>"}

  Successful response:
  {
      "website_id": "...",
      "tenant_id": "...",
      "domain": "...",
      "onboarding_status": "READY",
      "connector_authenticated": true,
      "backend_connected": true,
      "ready": true
  }

No production Data Engine is required.  All responses are fixture-based.
"""

from __future__ import annotations

import pytest

from tekjuice_connector.authentication.handshake import perform_handshake
from tekjuice_connector.client.client import DataEngineClient
from tekjuice_connector.client.timeouts import TimeoutConfig
from tekjuice_connector.exceptions.authentication import WebsiteIdMismatchError
from tekjuice_connector.exceptions.validation import ResponseValidationError
from tekjuice_connector.models.common import CONNECTOR_KEY_HEADER, HANDSHAKE_ENDPOINT
from tekjuice_connector.models.credentials import ConnectorCredentials
from tests.fixtures.handshake_fixtures import (
    FAKE_CONNECTOR_KEY,
    FAKE_DATA_ENGINE_URL,
    FAKE_WEBSITE_ID,
    HANDSHAKE_REQUEST_BODY,
    HANDSHAKE_RESPONSE_READY,
    HANDSHAKE_RESPONSE_PENDING,
    HANDSHAKE_RESPONSE_MISSING_READY,
    HANDSHAKE_RESPONSE_WRONG_WEBSITE_ID,
    HANDSHAKE_RESPONSE_NON_BOOL_FIELD,
)
from tests.fixtures.transport_fixtures import FakeTransport


def _make_client(transport: FakeTransport) -> DataEngineClient:
    creds = ConnectorCredentials(
        website_id=FAKE_WEBSITE_ID,
        connector_key=FAKE_CONNECTOR_KEY,
        data_engine_url=FAKE_DATA_ENGINE_URL,
    )
    return DataEngineClient(
        credentials=creds,
        timeout=TimeoutConfig(),
        transport=transport,
    )


# ---------------------------------------------------------------------------
# Request contract
# ---------------------------------------------------------------------------

class TestHandshakeRequestContract:
    def test_correct_endpoint_called(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_READY)])
        client = _make_client(transport)
        perform_handshake(client, FAKE_WEBSITE_ID)
        assert transport.last_path == HANDSHAKE_ENDPOINT

    def test_request_body_matches_contract(self):
        """Body must be exactly {"website_id": "<website_id>"}."""
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_READY)])
        client = _make_client(transport)
        perform_handshake(client, FAKE_WEBSITE_ID)
        assert transport.last_payload == HANDSHAKE_REQUEST_BODY

    def test_no_extra_fields_in_request(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_READY)])
        client = _make_client(transport)
        perform_handshake(client, FAKE_WEBSITE_ID)
        # Only 'website_id' should be present — no leaked credentials
        assert set(transport.last_payload.keys()) == {"website_id"}


# ---------------------------------------------------------------------------
# Response contract — required fields
# ---------------------------------------------------------------------------

class TestHandshakeResponseContract:
    _REQUIRED_FIELDS = (
        "website_id",
        "tenant_id",
        "domain",
        "onboarding_status",
        "connector_authenticated",
        "backend_connected",
        "ready",
    )

    def test_all_required_fields_present_in_ready_fixture(self):
        for field in self._REQUIRED_FIELDS:
            assert field in HANDSHAKE_RESPONSE_READY, f"Missing: {field}"

    def test_ready_response_parsed_correctly(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_READY)])
        client = _make_client(transport)
        resp = perform_handshake(client, FAKE_WEBSITE_ID)
        assert resp.website_id == FAKE_WEBSITE_ID
        assert resp.connector_authenticated is True
        assert resp.backend_connected is True
        assert resp.ready is True
        assert resp.onboarding_status == "READY"

    def test_pending_response_parsed_correctly(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_PENDING)])
        client = _make_client(transport)
        resp = perform_handshake(client, FAKE_WEBSITE_ID)
        assert resp.ready is False
        assert resp.onboarding_status == "PENDING"
        assert resp.connector_authenticated is True
        assert resp.backend_connected is False

    def test_missing_ready_field_raises_validation_error(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_MISSING_READY)])
        client = _make_client(transport)
        with pytest.raises(ResponseValidationError) as exc_info:
            perform_handshake(client, FAKE_WEBSITE_ID)
        assert "ready" in str(exc_info.value)

    def test_wrong_website_id_raises_mismatch(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_WRONG_WEBSITE_ID)])
        client = _make_client(transport)
        with pytest.raises(WebsiteIdMismatchError) as exc_info:
            perform_handshake(client, FAKE_WEBSITE_ID)
        assert FAKE_WEBSITE_ID in str(exc_info.value)
        assert "different-website-id" in str(exc_info.value)

    def test_non_boolean_field_raises_validation_error(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_NON_BOOL_FIELD)])
        client = _make_client(transport)
        with pytest.raises(ResponseValidationError):
            perform_handshake(client, FAKE_WEBSITE_ID)

    def test_non_dict_response_raises_validation_error(self):
        transport = FakeTransport(responses=[(200, ["unexpected", "list"])])
        client = _make_client(transport)
        with pytest.raises(ResponseValidationError):
            perform_handshake(client, FAKE_WEBSITE_ID)


# ---------------------------------------------------------------------------
# Security contract — secrets must not leak
# ---------------------------------------------------------------------------

class TestHandshakeSecurityContract:
    def test_connector_key_not_in_request_body(self):
        """The connector key must travel as a header, not in the JSON body."""
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_READY)])
        client = _make_client(transport)
        perform_handshake(client, FAKE_WEBSITE_ID)
        payload_str = str(transport.last_payload)
        assert FAKE_CONNECTOR_KEY not in payload_str

    def test_connector_key_not_in_exception_on_mismatch(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_WRONG_WEBSITE_ID)])
        client = _make_client(transport)
        try:
            perform_handshake(client, FAKE_WEBSITE_ID)
        except WebsiteIdMismatchError as exc:
            assert FAKE_CONNECTOR_KEY not in str(exc)

    def test_connector_key_not_in_validation_exception(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_MISSING_READY)])
        client = _make_client(transport)
        try:
            perform_handshake(client, FAKE_WEBSITE_ID)
        except ResponseValidationError as exc:
            assert FAKE_CONNECTOR_KEY not in str(exc)


# ---------------------------------------------------------------------------
# Idempotency contract
# ---------------------------------------------------------------------------

class TestHandshakeIdempotencyContract:
    def test_repeated_ready_handshake_returns_ready(self):
        """A READY website must remain READY on repeated calls."""
        transport = FakeTransport(
            responses=[
                (200, HANDSHAKE_RESPONSE_READY),
                (200, HANDSHAKE_RESPONSE_READY),
                (200, HANDSHAKE_RESPONSE_READY),
            ]
        )
        client = _make_client(transport)
        for _ in range(3):
            resp = perform_handshake(client, FAKE_WEBSITE_ID)
            assert resp.ready is True
        assert transport.call_count == 3
