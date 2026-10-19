"""Tek Juice Data Engine Connector.

The Connector is a standalone, framework-agnostic Python package that
customer backends install with ``pip install tekjuice-connector``.

It authenticates the customer backend with the Tek Juice Data Engine via
an E2E handshake, establishes a READY connection state, and exposes a
clean Python API and CLI for configuration, status inspection, and
diagnostics.

What the Connector does
-----------------------
- Loads configuration from environment variables.
- Validates credentials and Data Engine URL.
- Performs the E2E handshake: POST /api/v1/connector/handshake.
- Reports READY / FAILED / AUTHENTICATED etc. states.
- Provides bounded retries for transient failures.
- Sanitises all logs and diagnostic output (secrets never exposed).
- Provides a CLI: ``tekjuice status | test-connection | diagnostics``.

What the Connector does NOT do
-------------------------------
- It does NOT connect to the Data Engine database.
- It does NOT implement content generation or SEO.
- It does NOT store content itself: publication is received through
  ``tekjuice_connector.publication`` and persisted by a customer-provided
  ``ContentStore``.
- It does NOT require Django, FastAPI, Flask, or any other framework.

Quick start::

    from tekjuice_connector import TekJuiceConnector

    connector = TekJuiceConnector()   # reads env vars
    result = connector.connect()

    if result.ready:
        print("Tek Juice Data Engine connected")

Explicit configuration::

    connector = TekJuiceConnector(
        website_id="my-site",
        connector_key="...",          # use a secret manager, not a literal
        data_engine_url="https://engine.example.com",
    )
"""

from __future__ import annotations

from typing import Optional

from tekjuice_connector.config.settings import ConnectorSettings, load_settings
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.diagnostics.diagnostic import Diagnostician
from tekjuice_connector.diagnostics.report import DiagnosticReport
from tekjuice_connector.exceptions.api import ApiError, MalformedResponseError, ServerError
from tekjuice_connector.exceptions.authentication import (
    AuthenticationError,
    InvalidCredentialsError,
    WebsiteIdMismatchError,
)
from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.exceptions.configuration import (
    ConfigurationError,
    MissingConfigurationError,
)
from tekjuice_connector.exceptions.connection import (
    ConnectionError,
    ConnectionTimeoutError,
    RetryExhaustedError,
)
from tekjuice_connector.exceptions.validation import ResponseValidationError, ValidationError
from tekjuice_connector.models.connection import ConnectionResult, ConnectionState
from tekjuice_connector.publication.registration import PublicationRegistration
from tekjuice_connector.retry.policy import RetryPolicy
from tekjuice_connector.version import __version__


# ---------------------------------------------------------------------------
# Main public class
# ---------------------------------------------------------------------------


class TekJuiceConnector:
    """The primary entry point for the Tek Juice Data Engine Connector.

    Parameters
    ----------
    website_id:
        Your Tek Juice website ID.  Overrides ``TEKJUICE_WEBSITE_ID``.
    connector_key:
        Your connector key.  Overrides ``TEKJUICE_CONNECTOR_KEY``.
        Never log or print this value.
    data_engine_url:
        Base URL of the Tek Juice Data Engine API.
        Overrides ``TEKJUICE_DATA_ENGINE_URL``.
    connect_timeout:
        TCP connect timeout in seconds.  Default: 10.
    read_timeout:
        HTTP read timeout in seconds.  Default: 30.
    max_retries:
        Maximum retry attempts for transient failures.  Default: 3.
    retry_policy:
        Optional custom :class:`~tekjuice_connector.retry.policy.RetryPolicy`.
        When provided, ``max_retries`` is ignored.
    allow_http:
        Allow plain HTTP URLs in development.  Default: ``False``.
        In production, HTTPS is always required.
    publication:
        Optional :class:`~tekjuice_connector.publication.PublicationRegistration`
        describing this backend's publication receiver.  When given, every
        ``connect()`` registers it with the Data Engine, which then verifies
        it can publish before reporting ``ready``.

    Examples
    --------
    From environment::

        connector = TekJuiceConnector()
        result = connector.connect()

    Explicit::

        connector = TekJuiceConnector(
            website_id="acme-corp",
            connector_key=os.environ["MY_SECRET_KEY"],
            data_engine_url="https://engine.tekjuice.io",
        )
    """

    def __init__(
        self,
        *,
        website_id: Optional[str] = None,
        connector_key: Optional[str] = None,
        data_engine_url: Optional[str] = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        max_retries: int = 3,
        retry_policy: Optional[RetryPolicy] = None,
        allow_http: bool = False,
        publication: Optional["PublicationRegistration"] = None,
    ) -> None:
        self._settings: ConnectorSettings = load_settings(
            website_id=website_id,
            connector_key=connector_key,
            data_engine_url=data_engine_url,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_retries=max_retries,
        )

        # Apply security check on URL scheme.
        from tekjuice_connector.security.validation import validate_url_security
        validate_url_security(
            self._settings.credentials.data_engine_url,
            allow_http=allow_http,
        )

        effective_policy = retry_policy or RetryPolicy(max_attempts=max_retries)
        self._retry_policy = effective_policy
        self._last_setup = None

        self._manager: ConnectionManager = ConnectionManager.from_settings(
            self._settings,
            retry_policy=effective_policy,
            publication=publication,
        )

    # ------------------------------------------------------------------
    # Core public API
    # ------------------------------------------------------------------

    def connect(self) -> ConnectionResult:
        """Authenticate with the Data Engine and return a :class:`ConnectionResult`.

        Safe to call repeatedly — idempotent for websites already in READY
        state.  The Connector never attempts to move Data Engine onboarding
        state backwards.

        Returns
        -------
        ConnectionResult
            Typed result with ``ready``, ``state``, ``onboarding_status``,
            ``domain``, and ``tenant_id`` fields.

        Raises
        ------
        ConfigurationError
            When required configuration is missing or invalid.
        InvalidCredentialsError
            When the Data Engine rejects the connector key.
        WebsiteIdMismatchError
            When the response website_id does not match configuration.
        RetryExhaustedError
            When all retry attempts fail with transient errors.
        """
        return self._manager.connect()

    def status(self) -> ConnectionResult:
        """Return the last known connection result without a network call.

        Returns a DISCONNECTED result if ``connect()`` has not been called.

        Returns
        -------
        ConnectionResult
        """
        return self._manager.status()

    def diagnostics(self, *, include_connectivity: bool = True) -> DiagnosticReport:
        """Run the full diagnostic suite and return a :class:`DiagnosticReport`.

        Parameters
        ----------
        include_connectivity:
            When ``True`` (default) a live handshake is attempted.
            Pass ``False`` to limit diagnostics to configuration checks.

        Returns
        -------
        DiagnosticReport
            Structured report suitable for both programmatic inspection
            and human-readable output via ``report.to_text()``.
        """
        diagnostician = Diagnostician(
            settings=self._settings,
            manager=self._manager if include_connectivity else None,
            setup=getattr(self, "_last_setup", None),
        )
        return diagnostician.run()

    def setup(
        self,
        *,
        secret_store: object,
        receiver: object = None,
        app: object = None,
        api_base_url: Optional[str] = None,
        route_path: Optional[str] = None,
        register_route: bool = True,
        public_content_url: Optional[str] = None,
        rotate: bool = False,
        rollback_rotation: bool = False,
        allow_endpoint_change: bool = False,
        transport: object = None,
        **options: object,
    ):
        """Set up S2S publication end to end and return a
        :class:`~tekjuice_connector.setup.SetupResult` with evidence per stage.

        Generates the receiver secret on this server only when none is
        stored (or ``rotate=True``), persists it in *secret_store*,
        establishes ``TEKJUICE_PUBLICATION_ENDPOINT_URL`` from the configured
        API base URL and the receiver's route (mounting the route on *app*
        when the framework allows), proves the public endpoint accepts the
        secret with a dry run, registers both through the authenticated
        handshake and waits for the Data Engine's own verification.
        Publishes nothing. Idempotent; safe to call on every start-up.
        See ``docs/setup.md``.
        """
        from tekjuice_connector.authentication.authentication import Authenticator
        from tekjuice_connector.client.client import DataEngineClient
        from tekjuice_connector.setup.workflow import ConnectorSetup

        def handshake(registration):
            client = DataEngineClient.from_settings(self._settings, transport=transport)
            manager = ConnectionManager(
                Authenticator(client, self._settings.credentials, publication=registration),
                retry_policy=self._retry_policy,
            )
            result = manager.connect()
            # The latest Data Engine answer is also what status() reports.
            self._manager._last_result = result
            self._manager._state = result.state
            return result

        runner = ConnectorSetup(
            website_id=self._settings.credentials.website_id,
            handshake=handshake,
            secret_store=secret_store,
            receiver=receiver,
            app=app,
            api_base_url=api_base_url,
            route_path=route_path,
            register_route=register_route,
            public_content_url=public_content_url,
            **options,  # type: ignore[arg-type]
        )
        self._last_setup = runner.run(
            rotate=rotate, rollback_rotation=rollback_rotation, allow_endpoint_change=allow_endpoint_change
        )
        return getattr(self, "_last_setup", None)

    @property
    def last_setup(self):
        """The most recent :class:`~tekjuice_connector.setup.SetupResult`, or None."""
        return getattr(self, "_last_setup", None)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def settings(self) -> ConnectorSettings:
        """The validated connector settings (credentials redacted in repr)."""
        return self._settings

    @property
    def version(self) -> str:
        """Package version string."""
        return __version__

    # ------------------------------------------------------------------
    # Representation — never expose the connector key
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"TekJuiceConnector("
            f"website_id={self._settings.credentials.website_id!r}, "
            f"url={self._settings.credentials.data_engine_url!r}, "
            f"version={__version__!r})"
        )


# ---------------------------------------------------------------------------
# Public re-exports — keep the surface small
# ---------------------------------------------------------------------------

__all__ = [
    # Main class
    "TekJuiceConnector",
    # Result types
    "ConnectionResult",
    "ConnectionState",
    "DiagnosticReport",
    "PublicationRegistration",
    # Exception hierarchy
    "TekJuiceConnectorError",
    "ConfigurationError",
    "MissingConfigurationError",
    "ValidationError",
    "ResponseValidationError",
    "AuthenticationError",
    "InvalidCredentialsError",
    "WebsiteIdMismatchError",
    "ConnectionError",
    "ConnectionTimeoutError",
    "RetryExhaustedError",
    "ApiError",
    "ServerError",
    "MalformedResponseError",
    # Version
    "__version__",
]
