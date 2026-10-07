"""Connection manager — the central coordination point for all connect operations.

``ConnectionManager`` is the object that :class:`~tekjuice_connector.TekJuiceConnector`
delegates to.  It:

1. Holds current connection state.
2. Delegates authentication to :class:`~tekjuice_connector.authentication.Authenticator`.
3. Integrates the retry policy for transient failures.
4. Exposes ``connect()``, ``status()``, and ``last_session`` to callers.

Repeated calls to ``connect()`` are explicitly supported and safe.
A READY website remains READY; the Connector never attempts a backwards
state transition on the Data Engine.
"""

from __future__ import annotations

import logging
from typing import Optional

from tekjuice_connector.authentication.authentication import Authenticator
from tekjuice_connector.authentication.session import AuthSession
from tekjuice_connector.connection.lifecycle import build_result_from_session, derive_state
from tekjuice_connector.exceptions.authentication import (
    AuthenticationError,
    InvalidCredentialsError,
    WebsiteIdMismatchError,
)
from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.exceptions.connection import RetryExhaustedError
from tekjuice_connector.exceptions.validation import ResponseValidationError
from tekjuice_connector.models.connection import ConnectionResult, ConnectionState

_log = logging.getLogger(__name__)

# Exceptions that indicate a permanent failure — do not retry these.
_PERMANENT_ERRORS = (
    InvalidCredentialsError,
    WebsiteIdMismatchError,
    ResponseValidationError,
)


class ConnectionManager:
    """Manages the full connection lifecycle.

    Parameters
    ----------
    authenticator:
        The :class:`~tekjuice_connector.authentication.Authenticator` used
        to perform handshakes.
    retry_policy:
        Optional :class:`~tekjuice_connector.retry.policy.RetryPolicy`.
        When ``None`` a default policy is used.
    """

    def __init__(
        self,
        authenticator: Authenticator,
        retry_policy: Optional[object] = None,
    ) -> None:
        self._authenticator = authenticator
        self._retry_policy = retry_policy
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._last_session: Optional[AuthSession] = None
        self._last_result: Optional[ConnectionResult] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(self) -> ConnectionResult:
        """Authenticate with the Data Engine and return a :class:`ConnectionResult`.

        Safe to call repeatedly — a READY website simply returns a fresh
        READY result.  Transient failures are retried according to the
        configured retry policy.

        Returns
        -------
        ConnectionResult

        Raises
        ------
        InvalidCredentialsError
            Permanent failure — bad connector key.
        WebsiteIdMismatchError
            Permanent failure — response website_id mismatch.
        RetryExhaustedError
            All retry attempts failed with transient errors.
        """
        self._state = ConnectionState.CONNECTING
        _log.debug("Connection attempt starting (state=CONNECTING).")

        session = self._attempt_with_retry()

        self._last_session = session
        self._state = derive_state(session)
        result = build_result_from_session(session)
        self._last_result = result

        _log.debug(
            "Connection completed (state=%s, ready=%s).",
            self._state.value,
            result.ready,
        )
        return result

    def status(self) -> ConnectionResult:
        """Return the last known connection result without re-authenticating.

        Returns a DISCONNECTED result if ``connect()`` has not been called.
        """
        if self._last_result is not None:
            return self._last_result
        return ConnectionResult(
            state=self._state,
            ready=False,
            message="Not yet connected.",
        )

    @property
    def state(self) -> ConnectionState:
        """Current Connector-side connection state."""
        return self._state

    @property
    def last_session(self) -> Optional[AuthSession]:
        """The most recent successful :class:`AuthSession`, or ``None``."""
        return self._last_session

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _attempt_with_retry(self) -> AuthSession:
        """Run authenticate() with the retry policy applied."""
        if self._retry_policy is None:
            # Import here to avoid a circular dependency at module load time.
            from tekjuice_connector.retry.policy import RetryPolicy
            policy = RetryPolicy()
        else:
            policy = self._retry_policy  # type: ignore[assignment]

        last_exc: Optional[Exception] = None
        attempt = 0

        for attempt, delay in enumerate(policy.schedule(), start=1):
            if delay > 0:
                import time
                _log.debug(
                    "Waiting %.2fs before retry attempt %d.", delay, attempt
                )
                time.sleep(delay)

            try:
                return self._authenticator.authenticate()
            except _PERMANENT_ERRORS as exc:
                # Permanent — re-raise immediately, do not retry.
                _log.error(
                    "Permanent authentication failure on attempt %d: %s",
                    attempt,
                    exc,
                )
                raise
            except TekJuiceConnectorError as exc:
                last_exc = exc
                _log.warning(
                    "Transient connection failure on attempt %d/%d: %s",
                    attempt,
                    policy.max_attempts,
                    exc,
                )

        raise RetryExhaustedError(
            attempts=attempt,
            last_message=str(last_exc) if last_exc else "Unknown error",
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: object,
        retry_policy: Optional[object] = None,
    ) -> "ConnectionManager":
        """Build a :class:`ConnectionManager` from a :class:`ConnectorSettings`.

        Parameters
        ----------
        settings:
            A ``ConnectorSettings`` instance.
        retry_policy:
            Optional :class:`~tekjuice_connector.retry.policy.RetryPolicy`.
        """
        authenticator = Authenticator.from_settings(settings)
        return cls(authenticator=authenticator, retry_policy=retry_policy)
