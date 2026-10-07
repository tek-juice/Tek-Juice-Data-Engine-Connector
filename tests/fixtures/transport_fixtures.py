"""Fake HTTP transport for unit and integration tests.

Replaces the real ``requests``-based transport so tests never make network
calls.  All fixtures use placeholder credentials — no real secrets.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

from tekjuice_connector.client.http import HttpResponse, HttpTransport
from tekjuice_connector.exceptions.api import MalformedResponseError, ServerError
from tekjuice_connector.exceptions.authentication import InvalidCredentialsError
from tekjuice_connector.exceptions.connection import ConnectionTimeoutError
from tekjuice_connector.exceptions.connection import (
    ConnectionError as ConnectorConnectionError,
)


class FakeTransport:
    """An in-process transport that returns pre-configured responses.

    Parameters
    ----------
    responses:
        List of ``(status_code, body)`` tuples returned in order.
        When exhausted the last entry is repeated.
    raise_on_call:
        When set, raises this exception instead of returning a response.
    """

    def __init__(
        self,
        responses: Optional[List[Tuple[int, Any]]] = None,
        raise_on_call: Optional[Exception] = None,
    ) -> None:
        self._responses = responses or [(200, {})]
        self._raise = raise_on_call
        self._call_count = 0
        self.last_path: Optional[str] = None
        self.last_payload: Optional[Dict[str, Any]] = None

    def post(
        self,
        path: str,
        *,
        connector_key: str,
        payload: Dict[str, Any],
    ) -> HttpResponse:
        self._call_count += 1
        self.last_path = path
        self.last_payload = payload

        if self._raise is not None:
            raise self._raise

        idx = min(self._call_count - 1, len(self._responses) - 1)
        status_code, body = self._responses[idx]

        # Mirror the real transport's exception mapping.
        if status_code in (401, 403):
            raise InvalidCredentialsError(status_code=status_code)
        if status_code >= 500:
            raise ServerError(status_code=status_code, endpoint=path)

        return HttpResponse(status_code=status_code, body=body)

    @property
    def call_count(self) -> int:
        return self._call_count


def make_ready_transport(website_id: str = "test-website-123") -> FakeTransport:
    """Return a transport that always yields a READY handshake response."""
    body = {
        "website_id": website_id,
        "tenant_id": "tenant-001",
        "domain": "example.com",
        "onboarding_status": "READY",
        "connector_authenticated": True,
        "backend_connected": True,
        "ready": True,
    }
    return FakeTransport(responses=[(200, body)])


def make_timeout_transport(url: str = "https://engine.example.test") -> FakeTransport:
    """Return a transport that always raises a connection timeout."""
    return FakeTransport(
        raise_on_call=ConnectionTimeoutError(url=url, timeout=10.0)
    )


def make_server_error_transport() -> FakeTransport:
    """Return a transport that returns HTTP 500."""
    return FakeTransport(responses=[(500, {"error": "internal server error"})])


def make_invalid_credentials_transport() -> FakeTransport:
    """Return a transport that returns HTTP 401."""
    return FakeTransport(responses=[(401, {"error": "unauthorized"})])


def make_malformed_json_transport() -> FakeTransport:
    """Return a transport that returns HTTP 200 with a non-dict body."""
    return FakeTransport(responses=[(200, "not-a-dict")])
