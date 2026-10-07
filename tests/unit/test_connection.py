"""Unit tests — connection lifecycle, manager, health, status."""

from __future__ import annotations

import pytest

from tekjuice_connector.authentication.authentication import Authenticator
from tekjuice_connector.client.client import DataEngineClient
from tekjuice_connector.client.timeouts import TimeoutConfig
from tekjuice_connector.connection.health import HealthResult, check_health
from tekjuice_connector.connection.lifecycle import (
    derive_state,
    is_valid_transition,
    build_result_from_session,
)
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.connection.status import describe, is_failed, is_ready
from tekjuice_connector.exceptions.authentication import InvalidCredentialsError
from tekjuice_connector.exceptions.connection import (
    ConnectionTimeoutError,
    RetryExhaustedError,
)
from tekjuice_connector.models.authentication import HandshakeResponse
from tekjuice_connector.models.connection import ConnectionResult, ConnectionState
from tekjuice_connector.retry.policy import RetryPolicy
from tests.fixtures.handshake_fixtures import (
    FAKE_CONNECTOR_KEY,
    FAKE_DATA_ENGINE_URL,
    FAKE_WEBSITE_ID,
    HANDSHAKE_RESPONSE_PENDING,
    HANDSHAKE_RESPONSE_READY,
)
from tests.fixtures.transport_fixtures import (
    FakeTransport,
    make_invalid_credentials_transport,
    make_ready_transport,
    make_timeout_transport,
)
from tekjuice_connector.models.credentials import ConnectorCredentials


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(transport: FakeTransport, max_attempts: int = 1) -> ConnectionManager:
    creds = ConnectorCredentials(
        website_id=FAKE_WEBSITE_ID,
        connector_key=FAKE_CONNECTOR_KEY,
        data_engine_url=FAKE_DATA_ENGINE_URL,
    )
    client = DataEngineClient(
        credentials=creds,
        timeout=TimeoutConfig(connect=5.0, read=10.0),
        transport=transport,
    )
    auth = Authenticator(client=client, credentials=creds)
    policy = RetryPolicy(max_attempts=max_attempts, random_fn=lambda: 0.0)
    return ConnectionManager(authenticator=auth, retry_policy=policy)


# ---------------------------------------------------------------------------
# lifecycle helpers
# ---------------------------------------------------------------------------

class TestDeriveState:
    def _session(self, body: dict):
        from tekjuice_connector.authentication.session import AuthSession
        resp = HandshakeResponse.from_dict(body)
        return AuthSession.create(resp)

    def test_ready_response_gives_ready_state(self):
        session = self._session(HANDSHAKE_RESPONSE_READY)
        assert derive_state(session) is ConnectionState.READY

    def test_pending_response_gives_authenticated_state(self):
        session = self._session(HANDSHAKE_RESPONSE_PENDING)
        # connector_authenticated=True, backend_connected=False
        assert derive_state(session) is ConnectionState.AUTHENTICATED


class TestIsValidTransition:
    def test_disconnected_to_connecting(self):
        assert is_valid_transition(ConnectionState.DISCONNECTED, ConnectionState.CONNECTING)

    def test_ready_to_connecting_allowed(self):
        # reconnect/redeploy scenario
        assert is_valid_transition(ConnectionState.READY, ConnectionState.CONNECTING)

    def test_disconnected_to_ready_not_allowed(self):
        assert not is_valid_transition(ConnectionState.DISCONNECTED, ConnectionState.READY)

    def test_failed_to_connecting_allowed(self):
        assert is_valid_transition(ConnectionState.FAILED, ConnectionState.CONNECTING)


# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------

class TestConnectionManagerConnect:
    def test_ready_result(self):
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        result = manager.connect()
        assert result.ready is True
        assert result.state is ConnectionState.READY

    def test_state_updated_after_connect(self):
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        manager.connect()
        assert manager.state is ConnectionState.READY

    def test_last_session_populated(self):
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        manager.connect()
        assert manager.last_session is not None
        assert manager.last_session.ready is True

    def test_pending_result_not_ready(self):
        transport = FakeTransport(responses=[(200, HANDSHAKE_RESPONSE_PENDING)])
        manager = _make_manager(transport)
        result = manager.connect()
        assert result.ready is False
        assert result.state is ConnectionState.AUTHENTICATED

    def test_invalid_credentials_not_retried(self):
        transport = make_invalid_credentials_transport()
        manager = _make_manager(transport, max_attempts=3)
        with pytest.raises(InvalidCredentialsError):
            manager.connect()
        # Only 1 attempt — no retries for permanent auth failures
        assert transport.call_count == 1

    def test_timeout_exhausts_retries(self):
        transport = make_timeout_transport(FAKE_DATA_ENGINE_URL)
        manager = _make_manager(transport, max_attempts=3)
        with pytest.raises(RetryExhaustedError) as exc_info:
            manager.connect()
        assert exc_info.value.attempts == 3
        assert transport.call_count == 3

    def test_repeated_connect_succeeds(self):
        transport = FakeTransport(
            responses=[
                (200, HANDSHAKE_RESPONSE_READY),
                (200, HANDSHAKE_RESPONSE_READY),
            ]
        )
        manager = _make_manager(transport)
        r1 = manager.connect()
        r2 = manager.connect()
        assert r1.ready is True
        assert r2.ready is True

    def test_transient_then_success(self):
        """First call times out, second succeeds — with max_attempts=2."""
        timeout_exc = ConnectionTimeoutError(url=FAKE_DATA_ENGINE_URL, timeout=5.0)
        call_index = [0]
        original_post = None

        transport_success = make_ready_transport(FAKE_WEBSITE_ID)
        transport_fail = FakeTransport(raise_on_call=timeout_exc)

        # Build a transport that fails once then succeeds
        class OnceFailTransport:
            def __init__(self):
                self._calls = 0

            def post(self, path, *, connector_key, payload):
                self._calls += 1
                if self._calls == 1:
                    raise timeout_exc
                return transport_success.post(path, connector_key=connector_key, payload=payload)

        creds = ConnectorCredentials(
            website_id=FAKE_WEBSITE_ID,
            connector_key=FAKE_CONNECTOR_KEY,
            data_engine_url=FAKE_DATA_ENGINE_URL,
        )
        client = DataEngineClient(
            credentials=creds,
            timeout=TimeoutConfig(),
            transport=OnceFailTransport(),
        )
        auth = Authenticator(client=client, credentials=creds)
        policy = RetryPolicy(max_attempts=2, random_fn=lambda: 0.0)
        manager = ConnectionManager(authenticator=auth, retry_policy=policy)
        result = manager.connect()
        assert result.ready is True


class TestConnectionManagerStatus:
    def test_status_before_connect_is_disconnected(self):
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        result = manager.status()
        assert result.state is ConnectionState.DISCONNECTED
        assert result.ready is False

    def test_status_after_connect_reflects_last_result(self):
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        manager.connect()
        result = manager.status()
        assert result.ready is True


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

class TestStatusHelpers:
    def test_is_ready_true(self):
        result = ConnectionResult.from_handshake(
            HandshakeResponse.from_dict(HANDSHAKE_RESPONSE_READY)
        )
        assert is_ready(result) is True

    def test_is_ready_false_for_pending(self):
        result = ConnectionResult.from_handshake(
            HandshakeResponse.from_dict(HANDSHAKE_RESPONSE_PENDING)
        )
        assert is_ready(result) is False

    def test_is_failed_true(self):
        result = ConnectionResult.failed("error")
        assert is_failed(result) is True

    def test_describe_contains_state(self):
        result = ConnectionResult.from_handshake(
            HandshakeResponse.from_dict(HANDSHAKE_RESPONSE_READY)
        )
        desc = describe(result)
        assert "READY" in desc


# ---------------------------------------------------------------------------
# HealthResult / check_health
# ---------------------------------------------------------------------------

class TestHealthResult:
    def test_from_ready_result(self):
        result = ConnectionResult.from_handshake(
            HandshakeResponse.from_dict(HANDSHAKE_RESPONSE_READY)
        )
        health = HealthResult.from_connection_result(result)
        assert health.healthy is True
        assert health.state is ConnectionState.READY

    def test_from_error(self):
        exc = RuntimeError("boom")
        health = HealthResult.from_error(exc)
        assert health.healthy is False
        assert health.state is ConnectionState.FAILED
        assert health.error is exc


class TestCheckHealth:
    def test_ready_manager_healthy(self):
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        health = check_health(manager)
        assert health.healthy is True

    def test_failing_manager_unhealthy(self):
        manager = _make_manager(make_timeout_transport(FAKE_DATA_ENGINE_URL), max_attempts=1)
        health = check_health(manager)
        assert health.healthy is False
        assert health.state is ConnectionState.FAILED
