"""Shared pytest fixtures available across all test suites."""

from __future__ import annotations

import pytest

from tests.fixtures.handshake_fixtures import (
    FAKE_CONNECTOR_KEY,
    FAKE_DATA_ENGINE_URL,
    FAKE_WEBSITE_ID,
)
from tests.fixtures.transport_fixtures import (
    FakeTransport,
    make_invalid_credentials_transport,
    make_ready_transport,
    make_server_error_transport,
    make_timeout_transport,
)
from tekjuice_connector.authentication.authentication import Authenticator
from tekjuice_connector.client.client import DataEngineClient
from tekjuice_connector.config.settings import ConnectorSettings
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.models.credentials import ConnectorCredentials
from tekjuice_connector.retry.policy import RetryPolicy


@pytest.fixture
def fake_credentials() -> ConnectorCredentials:
    return ConnectorCredentials(
        website_id=FAKE_WEBSITE_ID,
        connector_key=FAKE_CONNECTOR_KEY,
        data_engine_url=FAKE_DATA_ENGINE_URL,
    )


@pytest.fixture
def fake_settings(fake_credentials) -> ConnectorSettings:
    return ConnectorSettings(
        credentials=fake_credentials,
        connect_timeout=5.0,
        read_timeout=10.0,
        max_retries=1,
    )


@pytest.fixture
def ready_transport() -> FakeTransport:
    return make_ready_transport(FAKE_WEBSITE_ID)


@pytest.fixture
def ready_client(fake_credentials, ready_transport) -> DataEngineClient:
    from tekjuice_connector.client.timeouts import TimeoutConfig
    return DataEngineClient(
        credentials=fake_credentials,
        timeout=TimeoutConfig(connect=5.0, read=10.0),
        transport=ready_transport,
    )


@pytest.fixture
def ready_authenticator(ready_client, fake_credentials) -> Authenticator:
    return Authenticator(client=ready_client, credentials=fake_credentials)


@pytest.fixture
def no_retry_policy() -> RetryPolicy:
    return RetryPolicy(max_attempts=1, random_fn=lambda: 0.0)


@pytest.fixture
def ready_manager(ready_authenticator, no_retry_policy) -> ConnectionManager:
    return ConnectionManager(
        authenticator=ready_authenticator,
        retry_policy=no_retry_policy,
    )
