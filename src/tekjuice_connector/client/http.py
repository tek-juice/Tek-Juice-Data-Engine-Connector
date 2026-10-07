"""Low-level HTTP transport layer.

Wraps the ``requests`` library and translates its exceptions into the
Connector's own typed exception hierarchy.  All secret-bearing headers are
constructed in ``headers.py`` and are never echoed in exceptions or logs.

``HttpTransport`` is the single object that speaks to the network.  It is
injected into higher-level objects (e.g. the handshake client) so that tests
can substitute a fake transport without patching global state.
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict, Optional

try:
    import requests
    from requests import Response, Session
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError
    from requests.exceptions import Timeout as RequestsTimeout
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The 'requests' package is required. Install it with: pip install requests"
    ) from exc

from tekjuice_connector.client.headers import build_request_headers, safe_headers_for_logging
from tekjuice_connector.client.timeouts import TimeoutConfig
from tekjuice_connector.exceptions.api import MalformedResponseError, ServerError
from tekjuice_connector.exceptions.authentication import InvalidCredentialsError
from tekjuice_connector.exceptions.connection import ConnectionTimeoutError
from tekjuice_connector.exceptions.connection import (
    ConnectionError as ConnectorConnectionError,
)
from tekjuice_connector.security.redaction import safe_url


class HttpResponse:
    """Minimal wrapper around a raw HTTP response.

    Decouples the rest of the package from the ``requests`` library so that
    tests can inject fake responses without importing ``requests``.
    """

    __slots__ = ("status_code", "body", "headers")

    def __init__(
        self,
        status_code: int,
        body: Any,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self.body = body  # already-parsed JSON (dict/list) or None
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def __repr__(self) -> str:
        return f"HttpResponse(status_code={self.status_code})"


class HttpTransport:
    """Sends HTTP requests and returns :class:`HttpResponse` objects.

    Parameters
    ----------
    base_url:
        Root URL for the Data Engine (no trailing slash).
    timeout:
        Connection and read timeouts.
    session:
        Optional pre-built ``requests.Session``.  Provide in tests to
        inject a mock session.
    """

    def __init__(
        self,
        base_url: str,
        timeout: TimeoutConfig,
        session: Optional[Session] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session: Session = session or requests.Session()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def post(
        self,
        path: str,
        *,
        connector_key: str,
        payload: Dict[str, Any],
    ) -> HttpResponse:
        """Send a POST request and return a parsed :class:`HttpResponse`.

        Parameters
        ----------
        path:
            API path, e.g. ``/api/v1/connector/handshake``.
        connector_key:
            The connector key used to build authentication headers.
            Never included in logs or exceptions.
        payload:
            JSON-serialisable request body.

        Returns
        -------
        HttpResponse

        Raises
        ------
        ConnectionTimeoutError
            On connect or read timeout.
        ConnectorConnectionError
            On other network-level failures (DNS, refused connection, etc.).
        MalformedResponseError
            When the response body cannot be parsed as JSON.
        ServerError
            On 5xx responses.
        InvalidCredentialsError
            On 401 / 403 responses.
        """
        url = f"{self._base_url}{path}"
        headers = build_request_headers(connector_key)

        try:
            raw: Response = self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._timeout.as_tuple(),
            )
        except RequestsTimeout as exc:
            raise ConnectionTimeoutError(
                url=safe_url(url), timeout=self._timeout.read
            ) from exc
        except RequestsConnectionError as exc:
            raise ConnectorConnectionError(
                f"Could not connect to Data Engine at '{safe_url(url)}': {exc}"
            ) from exc
        except Exception as exc:
            raise ConnectorConnectionError(
                f"Unexpected error during HTTP request to '{safe_url(url)}': {exc}"
            ) from exc

        return self._process_response(raw, path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _process_response(self, raw: Response, path: str) -> HttpResponse:
        """Parse and wrap a raw ``requests.Response``."""
        body: Any = None

        if raw.content:
            try:
                body = raw.json()
            except (ValueError, RequestsJSONDecodeError) as exc:
                raise MalformedResponseError(
                    endpoint=path,
                    detail=f"Response body is not valid JSON: {exc}",
                ) from exc

        if raw.status_code in (401, 403):
            raise InvalidCredentialsError(status_code=raw.status_code)

        if raw.status_code >= 500:
            raise ServerError(status_code=raw.status_code, endpoint=path)

        return HttpResponse(
            status_code=raw.status_code,
            body=body,
            headers=dict(raw.headers),
        )
