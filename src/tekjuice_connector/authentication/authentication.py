"""Authentication orchestrator.

``Authenticator`` combines credential loading, the HTTP client and the
handshake function into a single cohesive object used by the connection
manager.

Design notes
------------
- Repeated calls to :meth:`authenticate` are explicitly supported.  A
  website already in READY state returns a READY session; the Connector
  never attempts to move the onboarding state backwards.
- Authentication failures (4xx) are distinguished from transient network
  failures (timeouts, 5xx) so the retry layer can make the right choice.
- The connector key is never surfaced in logs or exceptions.
"""

from __future__ import annotations

from tekjuice_connector.authentication.handshake import perform_handshake
from tekjuice_connector.authentication.session import AuthSession
from tekjuice_connector.client.client import DataEngineClient
from tekjuice_connector.exceptions.authentication import (
    AuthenticationError,
    InvalidCredentialsError,
    WebsiteIdMismatchError,
)
from tekjuice_connector.exceptions.validation import ResponseValidationError
from tekjuice_connector.models.credentials import ConnectorCredentials


class Authenticator:
    """Performs the E2E handshake and produces an :class:`AuthSession`.

    Parameters
    ----------
    client:
        An authenticated :class:`~tekjuice_connector.client.DataEngineClient`.
    credentials:
        The connector credentials — used to supply ``website_id`` to the
        handshake and for logging/diagnostic purposes.
    """

    def __init__(
        self,
        client: DataEngineClient,
        credentials: ConnectorCredentials,
    ) -> None:
        self._client = client
        self._credentials = credentials

    def authenticate(self) -> AuthSession:
        """Execute the handshake and return a validated :class:`AuthSession`.

        This method is idempotent from the Connector's perspective: a
        website already in READY state will simply receive a READY response
        again, which is wrapped in a fresh ``AuthSession``.

        Returns
        -------
        AuthSession

        Raises
        ------
        InvalidCredentialsError
            When the Data Engine rejects the connector key.
        WebsiteIdMismatchError
            When the response website_id does not match configuration.
        ResponseValidationError
            When the response body fails contract validation.
        ConnectionTimeoutError / ConnectionError
            On transient network failures (let the retry layer handle these).
        ServerError
            On 5xx responses (let the retry layer handle these).
        """
        response = perform_handshake(
            client=self._client,
            website_id=self._credentials.website_id,
        )
        return AuthSession.create(response)

    @classmethod
    def from_settings(
        cls,
        settings: object,
        client: DataEngineClient | None = None,
    ) -> "Authenticator":
        """Convenience factory from a :class:`ConnectorSettings` object.

        Parameters
        ----------
        settings:
            A ``ConnectorSettings`` instance.
        client:
            Optional pre-built client.  Built automatically when omitted.
        """
        if client is None:
            client = DataEngineClient.from_settings(settings)  # type: ignore[arg-type]
        return cls(
            client=client,
            credentials=settings.credentials,  # type: ignore[union-attr]
        )
