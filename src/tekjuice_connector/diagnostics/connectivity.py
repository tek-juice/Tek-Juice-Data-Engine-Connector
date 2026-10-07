"""Connectivity diagnostic checks.

Performs a live handshake against the Data Engine and records the outcome.
Results are structured so they can be included in a :class:`DiagnosticReport`
without exposing the connector key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tekjuice_connector.exceptions.authentication import (
    AuthenticationError,
    InvalidCredentialsError,
    WebsiteIdMismatchError,
)
from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.exceptions.connection import ConnectionTimeoutError
from tekjuice_connector.exceptions.connection import RetryExhaustedError
from tekjuice_connector.exceptions.connection import (
    ConnectionError as ConnectorConnectionError,
)
from tekjuice_connector.models.connection import ConnectionResult, ConnectionState
from tekjuice_connector.security.redaction import safe_url


@dataclass
class ConnectivityCheckResult:
    """Outcome of a live connectivity check.

    Attributes
    ----------
    reachable:
        ``True`` when the Data Engine responded (even if auth failed).
    authenticated:
        ``True`` when the connector key was accepted.
    ready:
        ``True`` when the Data Engine reported READY.
    onboarding_status:
        Raw onboarding status string from the Data Engine, or ``None``.
    state:
        Connector-side :class:`ConnectionState`.
    error_category:
        Human-readable category string when a failure occurred.
    detail:
        Human-readable detail — never contains the connector key.
    """

    reachable: bool
    authenticated: bool
    ready: bool
    onboarding_status: Optional[str]
    state: ConnectionState
    error_category: Optional[str] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.ready

    def summary(self) -> str:
        parts = [
            f"Reachable: {self.reachable}",
            f"Authenticated: {self.authenticated}",
            f"Ready: {self.ready}",
        ]
        if self.onboarding_status:
            parts.append(f"Onboarding: {self.onboarding_status}")
        if self.error_category:
            parts.append(f"Error: {self.error_category} — {self.detail}")
        return " | ".join(parts)


def check_connectivity(manager: object) -> ConnectivityCheckResult:
    """Run a live connectivity check via *manager* without raising.

    Parameters
    ----------
    manager:
        A :class:`~tekjuice_connector.connection.manager.ConnectionManager`
        instance.

    Returns
    -------
    ConnectivityCheckResult
        Always returns — exceptions are caught and categorised.
    """
    try:
        result: ConnectionResult = manager.connect()  # type: ignore[union-attr]
        return ConnectivityCheckResult(
            reachable=True,
            authenticated=result.connector_authenticated or False,
            ready=result.ready,
            onboarding_status=result.onboarding_status,
            state=result.state,
            detail=result.message,
        )

    except ConnectionTimeoutError as exc:
        return ConnectivityCheckResult(
            reachable=False,
            authenticated=False,
            ready=False,
            onboarding_status=None,
            state=ConnectionState.FAILED,
            error_category="Timeout",
            detail=str(exc),
        )

    except RetryExhaustedError as exc:
        # Inspect last_message to surface the original failure category.
        category = "Timeout" if "timed out" in exc.last_message.lower() else "Network error"
        return ConnectivityCheckResult(
            reachable=False,
            authenticated=False,
            ready=False,
            onboarding_status=None,
            state=ConnectionState.FAILED,
            error_category=category,
            detail=str(exc),
        )

    except ConnectorConnectionError as exc:
        return ConnectivityCheckResult(
            reachable=False,
            authenticated=False,
            ready=False,
            onboarding_status=None,
            state=ConnectionState.FAILED,
            error_category="Network error",
            detail=str(exc),
        )

    except InvalidCredentialsError as exc:
        return ConnectivityCheckResult(
            reachable=True,
            authenticated=False,
            ready=False,
            onboarding_status=None,
            state=ConnectionState.FAILED,
            error_category="Invalid credentials",
            detail=str(exc),
        )

    except WebsiteIdMismatchError as exc:
        return ConnectivityCheckResult(
            reachable=True,
            authenticated=False,
            ready=False,
            onboarding_status=None,
            state=ConnectionState.FAILED,
            error_category="Website ID mismatch",
            detail=str(exc),
        )

    except AuthenticationError as exc:
        return ConnectivityCheckResult(
            reachable=True,
            authenticated=False,
            ready=False,
            onboarding_status=None,
            state=ConnectionState.FAILED,
            error_category="Authentication failure",
            detail=str(exc),
        )

    except TekJuiceConnectorError as exc:
        return ConnectivityCheckResult(
            reachable=False,
            authenticated=False,
            ready=False,
            onboarding_status=None,
            state=ConnectionState.FAILED,
            error_category="Connector error",
            detail=str(exc),
        )

    except Exception as exc:  # noqa: BLE001
        return ConnectivityCheckResult(
            reachable=False,
            authenticated=False,
            ready=False,
            onboarding_status=None,
            state=ConnectionState.FAILED,
            error_category="Unexpected error",
            detail=f"Unexpected error: {exc}",
        )
