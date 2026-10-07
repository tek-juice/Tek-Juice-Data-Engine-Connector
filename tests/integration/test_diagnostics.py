"""Integration tests — diagnostics with mocked transport."""

from __future__ import annotations

import pytest

from tekjuice_connector.config.settings import ConnectorSettings
from tekjuice_connector.diagnostics.configuration import check_configuration
from tekjuice_connector.diagnostics.connectivity import check_connectivity
from tekjuice_connector.diagnostics.diagnostic import Diagnostician
from tekjuice_connector.diagnostics.report import DiagnosticReport
from tekjuice_connector.models.credentials import ConnectorCredentials
from tekjuice_connector.models.connection import ConnectionState
from tekjuice_connector.security.redaction import REDACTED_MARKER
from tests.fixtures.handshake_fixtures import (
    FAKE_CONNECTOR_KEY,
    FAKE_DATA_ENGINE_URL,
    FAKE_WEBSITE_ID,
)
from tests.fixtures.transport_fixtures import (
    make_invalid_credentials_transport,
    make_ready_transport,
    make_timeout_transport,
)
from tekjuice_connector.authentication.authentication import Authenticator
from tekjuice_connector.client.client import DataEngineClient
from tekjuice_connector.client.timeouts import TimeoutConfig
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.retry.policy import RetryPolicy


def _make_manager(transport, max_attempts=1):
    creds = ConnectorCredentials(
        website_id=FAKE_WEBSITE_ID,
        connector_key=FAKE_CONNECTOR_KEY,
        data_engine_url=FAKE_DATA_ENGINE_URL,
    )
    client = DataEngineClient(credentials=creds, timeout=TimeoutConfig(), transport=transport)
    auth = Authenticator(client=client, credentials=creds)
    policy = RetryPolicy(max_attempts=max_attempts, random_fn=lambda: 0.0)
    return ConnectionManager(authenticator=auth, retry_policy=policy)


# ---------------------------------------------------------------------------
# check_configuration
# ---------------------------------------------------------------------------

class TestCheckConfiguration:
    def test_all_valid(self):
        result = check_configuration(FAKE_WEBSITE_ID, FAKE_CONNECTOR_KEY, FAKE_DATA_ENGINE_URL)
        assert result.ok is True
        assert len(result.checks) == 3

    def test_missing_website_id(self):
        result = check_configuration(None, FAKE_CONNECTOR_KEY, FAKE_DATA_ENGINE_URL)
        assert result.ok is False
        failed = [c for c in result.checks if not c.ok]
        assert any("WEBSITE_ID" in c.name for c in failed)

    def test_connector_key_display_always_redacted(self):
        result = check_configuration(FAKE_WEBSITE_ID, FAKE_CONNECTOR_KEY, FAKE_DATA_ENGINE_URL)
        key_check = next(c for c in result.checks if "CONNECTOR_KEY" in c.name)
        assert key_check.display_value == REDACTED_MARKER
        assert FAKE_CONNECTOR_KEY not in key_check.detail

    def test_missing_key_check_still_redacted(self):
        result = check_configuration(FAKE_WEBSITE_ID, None, FAKE_DATA_ENGINE_URL)
        key_check = next(c for c in result.checks if "CONNECTOR_KEY" in c.name)
        assert FAKE_CONNECTOR_KEY not in key_check.detail

    def test_summary_no_key(self):
        result = check_configuration(FAKE_WEBSITE_ID, FAKE_CONNECTOR_KEY, FAKE_DATA_ENGINE_URL)
        assert FAKE_CONNECTOR_KEY not in result.summary()


# ---------------------------------------------------------------------------
# check_connectivity
# ---------------------------------------------------------------------------

class TestCheckConnectivity:
    def test_ready_transport_is_reachable_and_ready(self):
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        result = check_connectivity(manager)
        assert result.reachable is True
        assert result.authenticated is True
        assert result.ready is True
        assert result.ok is True

    def test_timeout_not_reachable(self):
        manager = _make_manager(make_timeout_transport(FAKE_DATA_ENGINE_URL))
        result = check_connectivity(manager)
        assert result.reachable is False
        assert result.error_category == "Timeout"

    def test_invalid_credentials_reachable_not_authenticated(self):
        manager = _make_manager(make_invalid_credentials_transport())
        result = check_connectivity(manager)
        assert result.reachable is True
        assert result.authenticated is False
        assert result.error_category == "Invalid credentials"

    def test_summary_no_key(self):
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        result = check_connectivity(manager)
        assert FAKE_CONNECTOR_KEY not in result.summary()


# ---------------------------------------------------------------------------
# DiagnosticReport
# ---------------------------------------------------------------------------

class TestDiagnosticReport:
    def _make_settings(self):
        creds = ConnectorCredentials(
            website_id=FAKE_WEBSITE_ID,
            connector_key=FAKE_CONNECTOR_KEY,
            data_engine_url=FAKE_DATA_ENGINE_URL,
        )
        return ConnectorSettings(credentials=creds)

    def test_config_only_report(self):
        settings = self._make_settings()
        d = Diagnostician(settings=settings, manager=None)
        report = d.run()
        assert report.config.ok is True
        assert report.connectivity is None
        assert report.overall_ok is False  # no connectivity check

    def test_full_report_ready(self):
        settings = self._make_settings()
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        d = Diagnostician(settings=settings, manager=manager)
        report = d.run()
        assert report.overall_ok is True

    def test_full_report_timeout(self):
        settings = self._make_settings()
        manager = _make_manager(make_timeout_transport(FAKE_DATA_ENGINE_URL))
        d = Diagnostician(settings=settings, manager=manager)
        report = d.run()
        assert report.overall_ok is False

    def test_to_text_no_key(self):
        settings = self._make_settings()
        d = Diagnostician(settings=settings, manager=None)
        report = d.run()
        text = report.to_text()
        assert FAKE_CONNECTOR_KEY not in text
        assert "Tek Juice" in text

    def test_to_text_contains_verdict(self):
        settings = self._make_settings()
        manager = _make_manager(make_ready_transport(FAKE_WEBSITE_ID))
        d = Diagnostician(settings=settings, manager=manager)
        report = d.run()
        text = report.to_text()
        assert "READY" in text
