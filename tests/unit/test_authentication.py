"""Unit tests — authentication: handshake validation and Authenticator."""

from __future__ import annotations

import pytest

from tekjuice_connector.authentication.handshake import perform_handshake
from tekjuice_connector.authentication.session import AuthSession
from tekjuice_connector.client.client import DataEngineClient
from tekjuice_connector.client.timeouts import TimeoutConfig
from tekjuice_connector.exceptions.authentication import (
    InvalidCredentialsError,
    WebsiteIdMismatchError,
)
from tekjuice_connector.exceptions.validation import ResponseValidationError
from tekjuice_connector.models.authentication import HandshakeResponse
from tests.fixtures.handshake_fixtures import (
    FAKE_CONNECTOR_KEY,
    FAKE_DATA_ENGINE_URL,
    FAKE_WEBSITE_ID,
    HANDSHAKE_RESPONSE_NON_BOOL_FIELD,
    HANDSHAKE_RESPONSE_PENDING,
    HANDSHAKE_RESPONSE_READY,
    HANDSHAKE_RESPONSE_WRONG_WEBSITE_ID,
    HANDSHAKE_RESPONSE_MISSING_READY,
)
from tests.fixtures.transport_fixtures import (
    FakeTransport,
    make_invalid_credentials_transport,
    make_ready_transport,
)
from tekjuice_connector.models.credentials import ConnectorCredentials


def _make_client(transport: FakeTransport) -> DataEngineClient:
    creds = ConnectorCredentials(
        website_id=FAKE_WEBSITE_ID,
        connector_key=FAKE_CONNECTOR_KEY,
        data_engine_url=FAKE_DATA_ENGINE_URL,
    )
    return DataEngineClient(
        credentials=creds,
        timeout=TimeoutConfig(connect=5.0, read=10.0),
        transport=transport,
    )


# ---------------------------------------------------------------------------
# perform_handshake
# ---------------------------------------------------------------------------

class TestPerformHandshake:
    def test_ready_response_succeeds(self):
        transport = make_ready_transport(FAKE_WEBSITE_ID)
        client = _make_client(transport)
        response = perform_handshake(client, FAKE_WEBSITE_ID)
        assert response.ready is True
        assert response.website_id == FAKE_WEBSITE_ID

    def test_pending_response_returns_not_ready(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_PENDING)])
        client = _make_client(transport)
        response = perform_handshake(client, FAKE_WEBSITE_ID)
        assert response.ready is False
        assert response.onboarding_status == "PENDING"

    def test_website_id_mismatch_raises(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_WRONG_WEBSITE_ID)])
        client = _make_client(transport)
        with pytest.raises(WebsiteIdMismatchError) as exc_info:
            perform_handshake(client, FAKE_WEBSITE_ID)
        assert FAKE_WEBSITE_ID in str(exc_info.value)

    def test_missing_required_field_raises(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_MISSING_READY)])
        client = _make_client(transport)
        with pytest.raises(ResponseValidationError) as exc_info:
            perform_handshake(client, FAKE_WEBSITE_ID)
        assert "ready" in str(exc_info.value)

    def test_non_bool_field_raises(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_NON_BOOL_FIELD)])
        client = _make_client(transport)
        with pytest.raises(ResponseValidationError):
            perform_handshake(client, FAKE_WEBSITE_ID)

    def test_non_dict_body_raises(self):
        transport = FakeTransport(responses=[(200, "plain string")])
        client = _make_client(transport)
        with pytest.raises(ResponseValidationError):
            perform_handshake(client, FAKE_WEBSITE_ID)

    def test_invalid_credentials_propagated(self):
        transport = make_invalid_credentials_transport()
        client = _make_client(transport)
        with pytest.raises(InvalidCredentialsError):
            perform_handshake(client, FAKE_WEBSITE_ID)

    def test_connector_key_not_in_exception(self):
        transport = make_invalid_credentials_transport()
        client = _make_client(transport)
        try:
            perform_handshake(client, FAKE_WEBSITE_ID)
        except InvalidCredentialsError as exc:
            assert FAKE_CONNECTOR_KEY not in str(exc)


# ---------------------------------------------------------------------------
# AuthSession
# ---------------------------------------------------------------------------

class TestAuthSession:
    def _make_session(self) -> AuthSession:
        response = HandshakeResponse.from_dict(HANDSHAKE_RESPONSE_READY)
        return AuthSession.create(response)

    def test_ready_property(self):
        session = self._make_session()
        assert session.ready is True

    def test_website_id_property(self):
        session = self._make_session()
        assert session.website_id == FAKE_WEBSITE_ID

    def test_authenticated_at_set(self):
        session = self._make_session()
        assert session.authenticated_at is not None

    def test_repr_no_key(self):
        session = self._make_session()
        assert FAKE_CONNECTOR_KEY not in repr(session)

    def test_repr_contains_state(self):
        session = self._make_session()
        assert "ready=True" in repr(session)


# ---------------------------------------------------------------------------
# Repeated authentication (idempotency)
# ---------------------------------------------------------------------------

class TestRepeatedAuthentication:
    def test_second_call_succeeds(self):
        """A READY website stays READY on repeated handshake calls."""
        transport = FakeTransport(
            responses=[
                (200, HANDSHAKE_RESPONSE_READY),
                (200, HANDSHAKE_RESPONSE_READY),
            ]
        )
        client = _make_client(transport)
        r1 = perform_handshake(client, FAKE_WEBSITE_ID)
        r2 = perform_handshake(client, FAKE_WEBSITE_ID)
        assert r1.ready is True
        assert r2.ready is True
        assert transport.call_count == 2
