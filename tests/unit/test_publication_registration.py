"""Unit tests — publication registration through the connector handshake."""

from __future__ import annotations

import logging

import pytest

from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.authentication.authentication import Authenticator
from tekjuice_connector.client.client import DataEngineClient
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.exceptions.api import ApiError
from tekjuice_connector.exceptions.configuration import (
    ConfigurationError,
    MissingConfigurationError,
)
from tekjuice_connector.models.authentication import HandshakeRequest
from tekjuice_connector.models.credentials import ConnectorCredentials
from tekjuice_connector.publication import (
    InMemoryContentStore,
    PublicationReceiver,
    PublicationRegistration,
)
from tekjuice_connector.retry.policy import RetryPolicy
from tests.fixtures.handshake_fixtures import (
    FAKE_CONNECTOR_KEY,
    FAKE_DATA_ENGINE_URL,
    FAKE_WEBSITE_ID,
)
from tests.fixtures.publication_fixtures import PUBLIC_BASE_URL, RECEIVER_SECRET
from tests.fixtures.transport_fixtures import FakeTransport

ENDPOINT = "https://api.customer.example/tekjuice/publish"


def _receiver(**store_kwargs):
    return PublicationReceiver(
        store=InMemoryContentStore(PUBLIC_BASE_URL, **store_kwargs),
        website_id=FAKE_WEBSITE_ID,
        receiver_secret=RECEIVER_SECRET,
    )


def _handshake_body(**overrides):
    body = {
        "website_id": FAKE_WEBSITE_ID,
        "tenant_id": "tenant-001",
        "domain": "customer.example",
        "onboarding_status": "BACKEND_CONNECTED",
        "connector_authenticated": True,
        "backend_connected": True,
        "ready": False,
        "publication_status": "pending_verification",
        "publication_ready": False,
        "next_action": "await_publication_verification",
    }
    body.update(overrides)
    return body


def _manager(transport, registration, attempts=1):
    credentials = ConnectorCredentials(
        website_id=FAKE_WEBSITE_ID,
        connector_key=FAKE_CONNECTOR_KEY,
        data_engine_url=FAKE_DATA_ENGINE_URL,
    )
    client = DataEngineClient(credentials=credentials, transport=transport)
    return ConnectionManager(
        authenticator=Authenticator(client, credentials, publication=registration),
        retry_policy=RetryPolicy(max_attempts=attempts, random_fn=lambda: 0.0),
    )


def test_registration_reflects_receiver_capabilities():
    registration = PublicationRegistration.from_receiver(
        _receiver(supports_update=False), endpoint_url=ENDPOINT, framework="flask"
    )

    assert registration.to_handshake_dict() == {
        "endpoint_url": ENDPOINT,
        "auth_scheme": "bearer",
        "receiver_secret": RECEIVER_SECRET,
        "contract_versions": ["1.0"],
        "capabilities": ["publish.create", "publish.verify"],
        "framework": "flask",
    }


def test_registration_endpoint_from_environment(monkeypatch):
    monkeypatch.setenv("TEKJUICE_PUBLICATION_ENDPOINT_URL", ENDPOINT)
    registration = PublicationRegistration.from_receiver(_receiver())
    assert registration.endpoint_url == ENDPOINT


def test_registration_requires_endpoint(monkeypatch):
    monkeypatch.delenv("TEKJUICE_PUBLICATION_ENDPOINT_URL", raising=False)
    with pytest.raises(MissingConfigurationError):
        PublicationRegistration.from_receiver(_receiver())


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.customer.example/publish",
        "https://user:pw@api.customer.example/publish",
        "/relative/publish",
    ],
)
def test_registration_rejects_unsafe_endpoints(endpoint):
    with pytest.raises(ConfigurationError):
        PublicationRegistration.from_receiver(_receiver(), endpoint_url=endpoint)


def test_registration_and_request_never_expose_secret():
    registration = PublicationRegistration.from_receiver(_receiver(), endpoint_url=ENDPOINT)
    request = HandshakeRequest(
        website_id=FAKE_WEBSITE_ID, publication=registration.to_handshake_dict()
    )

    assert RECEIVER_SECRET not in repr(registration)
    assert RECEIVER_SECRET not in repr(request)


def test_handshake_sends_registration_and_parses_publication_state():
    transport = FakeTransport(responses=[(200, _handshake_body())])
    registration = PublicationRegistration.from_receiver(_receiver(), endpoint_url=ENDPOINT)

    result = _manager(transport, registration).connect()

    assert transport.last_payload["website_id"] == FAKE_WEBSITE_ID
    assert transport.last_payload["publication"]["endpoint_url"] == ENDPOINT
    assert transport.last_payload["publication"]["receiver_secret"] == RECEIVER_SECRET
    assert result.ready is False
    assert result.backend_connected is True
    assert result.publication_status == "pending_verification"
    assert result.publication_ready is False
    assert result.next_action == "await_publication_verification"


def test_handshake_without_registration_is_unchanged():
    transport = FakeTransport(
        responses=[(200, {k: v for k, v in _handshake_body().items()
                          if not k.startswith(("publication", "next"))})]
    )
    result = _manager(transport, None).connect()

    assert "publication" not in transport.last_payload
    assert result.publication_status is None
    assert result.publication_ready is None


def test_ready_only_when_data_engine_reports_ready():
    transport = FakeTransport(
        responses=[(200, _handshake_body(
            onboarding_status="READY", ready=True,
            publication_status="verified", publication_ready=True, next_action=None,
        ))]
    )
    registration = PublicationRegistration.from_receiver(_receiver(), endpoint_url=ENDPOINT)

    result = _manager(transport, registration).connect()

    assert result.ready is True
    assert result.publication_ready is True


def test_data_engine_4xx_is_permanent_and_actionable():
    transport = FakeTransport(
        responses=[(409, {"error": {"code": "ONBOARDING_PREREQUISITE_MISSING",
                                    "message": "Website ownership must be verified"}})]
    )
    registration = PublicationRegistration.from_receiver(_receiver(), endpoint_url=ENDPOINT)

    with pytest.raises(ApiError, match="ownership must be verified") as excinfo:
        _manager(transport, registration, attempts=3).connect()

    assert excinfo.value.status_code == 409
    assert transport.call_count == 1


def test_connector_accepts_publication_registration():
    registration = PublicationRegistration.from_receiver(_receiver(), endpoint_url=ENDPOINT)
    connector = TekJuiceConnector(
        website_id=FAKE_WEBSITE_ID,
        connector_key=FAKE_CONNECTOR_KEY,
        data_engine_url=FAKE_DATA_ENGINE_URL,
        publication=registration,
    )
    assert RECEIVER_SECRET not in repr(connector)


def test_receiver_secret_is_redacted_from_logs(caplog):
    from tekjuice_connector.security.redaction import redact_env_value

    assert redact_env_value("TEKJUICE_RECEIVER_SECRET", RECEIVER_SECRET) != RECEIVER_SECRET
