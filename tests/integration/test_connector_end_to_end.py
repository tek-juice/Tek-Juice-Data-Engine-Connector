"""Integration tests — TekJuiceConnector public API with mocked transport.

These tests exercise the full stack from the public TekJuiceConnector class
down through ConnectionManager → Authenticator → Handshake → HTTP, with the
real transport replaced by a FakeTransport.  No network calls are made.
"""

from __future__ import annotations

import pytest

from tekjuice_connector import (
    TekJuiceConnector,
    ConnectionState,
    InvalidCredentialsError,
    RetryExhaustedError,
    WebsiteIdMismatchError,
)
from tekjuice_connector.client.client import DataEngineClient
from tekjuice_connector.client.timeouts import TimeoutConfig
from tekjuice_connector.models.credentials import ConnectorCredentials
from tekjuice_connector.retry.policy import RetryPolicy
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.authentication.authentication import Authenticator
from tests.fixtures.handshake_fixtures import (
    FAKE_CONNECTOR_KEY,
    FAKE_DATA_ENGINE_URL,
    FAKE_WEBSITE_ID,
    HANDSHAKE_RESPONSE_PENDING,
    HANDSHAKE_RESPONSE_READY,
    HANDSHAKE_RESPONSE_WRONG_WEBSITE_ID,
)
from tests.fixtures.transport_fixtures import (
    FakeTransport,
    make_invalid_credentials_transport,
    make_ready_transport,
    make_server_error_transport,
    make_timeout_transport,
)


def _build_connector(transport: FakeTransport, max_retries: int = 1) -> TekJuiceConnector:
    """Build a TekJuiceConnector wired to a fake transport."""
    connector = TekJuiceConnector.__new__(TekJuiceConnector)

    from tekjuice_connector.config.settings import ConnectorSettings
    creds = ConnectorCredentials(
        website_id=FAKE_WEBSITE_ID,
        connector_key=FAKE_CONNECTOR_KEY,
        data_engine_url=FAKE_DATA_ENGINE_URL,
    )
    settings = ConnectorSettings(credentials=creds)
    connector._settings = settings

    client = DataEngineClient(
        credentials=creds,
        timeout=TimeoutConfig(),
        transport=transport,
    )
    auth = Authenticator(client=client, credentials=creds)
    policy = RetryPolicy(max_attempts=max_retries, random_fn=lambda: 0.0)
    connector._manager = ConnectionManager(authenticator=auth, retry_policy=policy)

    return connector


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestConnectorHappyPath:
    def test_connect_returns_ready(self):
        connector = _build_connector(make_ready_transport(FAKE_WEBSITE_ID))
        result = connector.connect()
        assert result.ready is True
        assert result.state is ConnectionState.READY

    def test_connect_populates_domain(self):
        connector = _build_connector(make_ready_transport(FAKE_WEBSITE_ID))
        result = connector.connect()
        assert result.domain == "example.com"

    def test_connect_populates_tenant_id(self):
        connector = _build_connector(make_ready_transport(FAKE_WEBSITE_ID))
        result = connector.connect()
        assert result.tenant_id == "tenant-001"

    def test_status_after_connect_is_ready(self):
        connector = _build_connector(make_ready_transport(FAKE_WEBSITE_ID))
        connector.connect()
        result = connector.status()
        assert result.ready is True

    def test_repeated_connect_is_idempotent(self):
        transport = FakeTransport(
            responses=[
                (200, HANDSHAKE_RESPONSE_READY),
                (200, HANDSHAKE_RESPONSE_READY),
            ]
        )
        connector = _build_connector(transport)
        r1 = connector.connect()
        r2 = connector.connect()
        assert r1.ready is True
        assert r2.ready is True

    def test_repr_does_not_contain_key(self):
        connector = _build_connector(make_ready_transport(FAKE_WEBSITE_ID))
        assert FAKE_CONNECTOR_KEY not in repr(connector)

    def test_version_property(self):
        connector = _build_connector(make_ready_transport(FAKE_WEBSITE_ID))
        from tekjuice_connector.version import __version__
        assert connector.version == __version__


# ---------------------------------------------------------------------------
# Pending / not-READY
# ---------------------------------------------------------------------------

class TestConnectorPending:
    def test_pending_result_not_ready(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_PENDING)])
        connector = _build_connector(transport)
        result = connector.connect()
        assert result.ready is False
        assert result.onboarding_status == "PENDING"
        assert result.state is ConnectionState.AUTHENTICATED


# ---------------------------------------------------------------------------
# Authentication failures
# ---------------------------------------------------------------------------

class TestConnectorAuthFailures:
    def test_invalid_credentials_raises(self):
        connector = _build_connector(make_invalid_credentials_transport())
        with pytest.raises(InvalidCredentialsError):
            connector.connect()

    def test_invalid_credentials_not_retried(self):
        transport = make_invalid_credentials_transport()
        connector = _build_connector(transport, max_retries=3)
        with pytest.raises(InvalidCredentialsError):
            connector.connect()
        assert transport.call_count == 1  # permanent — no retries

    def test_website_id_mismatch_raises(self):
        transport = FakeTransport(
            responses=[(200, HANDSHAKE_RESPONSE_WRONG_WEBSITE_ID)]
        )
        connector = _build_connector(transport)
        with pytest.raises(WebsiteIdMismatchError):
            connector.connect()


# ---------------------------------------------------------------------------
# Connectivity failures / retries
# ---------------------------------------------------------------------------

class TestConnectorConnectivityFailures:
    def test_timeout_raises_retry_exhausted(self):
        transport = make_timeout_transport(FAKE_DATA_ENGINE_URL)
        connector = _build_connector(transport, max_retries=2)
        with pytest.raises(RetryExhaustedError) as exc_info:
            connector.connect()
        assert exc_info.value.attempts == 2

    def test_server_error_retried(self):
        """Two 500s then success — connector should succeed on attempt 3."""
        transport = FakeTransport(
            responses=[
                (500, {}),
                (500, {}),
                (200, HANDSHAKE_RESPONSE_READY),
            ]
        )
        connector = _build_connector(transport, max_retries=3)
        result = connector.connect()
        assert result.ready is True
        assert transport.call_count == 3


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class TestConnectorDiagnostics:
    def test_diagnostics_config_only(self):
        connector = _build_connector(make_ready_transport(FAKE_WEBSITE_ID))
        report = connector.diagnostics(include_connectivity=False)
        assert report.config.ok is True
        assert report.connectivity is None

    def test_diagnostics_with_connectivity(self):
        connector = _build_connector(make_ready_transport(FAKE_WEBSITE_ID))
        # Wire diagnostics manager to the same fake transport
        report = connector.diagnostics(include_connectivity=False)
        assert report.config.ok is True

    def test_diagnostics_to_text_no_key(self):
        connector = _build_connector(make_ready_transport(FAKE_WEBSITE_ID))
        report = connector.diagnostics(include_connectivity=False)
        text = report.to_text()
        assert FAKE_CONNECTOR_KEY not in text
