"""High-level HTTP client used by the authentication and diagnostic layers.

``DataEngineClient`` is the package's single point of entry for all
outbound HTTP communication.  It combines the transport, timeouts and
credential handling in one place so that callers never have to deal with
low-level HTTP details.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from tekjuice_connector.client.http import HttpResponse, HttpTransport
from tekjuice_connector.client.timeouts import TimeoutConfig
from tekjuice_connector.models.credentials import ConnectorCredentials


class DataEngineClient:
    """Authenticated HTTP client for the Tek Juice Data Engine API.

    Parameters
    ----------
    credentials:
        Validated connector credentials.  The connector key is used to
        build authorization headers and is never logged.
    timeout:
        Optional timeout override.  Defaults are taken from the credentials
        object's enclosing settings when this client is built via
        :meth:`from_settings`.
    transport:
        Optional custom :class:`~tekjuice_connector.client.http.HttpTransport`.
        Inject a fake transport in tests.
    """

    def __init__(
        self,
        credentials: ConnectorCredentials,
        timeout: Optional[TimeoutConfig] = None,
        transport: Optional[HttpTransport] = None,
    ) -> None:
        self._credentials = credentials
        self._timeout = timeout or TimeoutConfig()
        self._transport = transport or HttpTransport(
            base_url=credentials.data_engine_url,
            timeout=self._timeout,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(cls, settings: Any, transport: Optional[HttpTransport] = None) -> "DataEngineClient":
        """Build a client from a :class:`~tekjuice_connector.config.ConnectorSettings`.

        Parameters
        ----------
        settings:
            A ``ConnectorSettings`` instance.
        transport:
            Optional transport override for testing.
        """
        timeout = TimeoutConfig(
            connect=settings.connect_timeout,
            read=settings.read_timeout,
        )
        return cls(
            credentials=settings.credentials,
            timeout=timeout,
            transport=transport,
        )

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    def post(self, path: str, payload: Dict[str, Any]) -> HttpResponse:
        """Send an authenticated POST request.

        Parameters
        ----------
        path:
            The API path, e.g. ``/api/v1/connector/handshake``.
        payload:
            JSON-serialisable request body.

        Returns
        -------
        HttpResponse
            The parsed response.  All HTTP/network exceptions are raised
            as typed Connector exceptions by the transport layer.
        """
        return self._transport.post(
            path,
            connector_key=self._credentials.connector_key,
            payload=payload,
        )

    # ------------------------------------------------------------------
    # Representation — never expose the connector key
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"DataEngineClient("
            f"url={self._credentials.data_engine_url!r}, "
            f"timeout={self._timeout!r})"
        )
