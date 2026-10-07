"""Health check utilities.

Provides a :func:`check_health` function that attempts a handshake and
returns a structured health result without raising.  Intended for use by
diagnostics and monitoring integrations that need a non-throwing interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.models.connection import ConnectionResult, ConnectionState


@dataclass
class HealthResult:
    """Result of a health check operation.

    Attributes
    ----------
    healthy:
        ``True`` when the Connector is READY.
    state:
        The :class:`ConnectionState` at the time of the check.
    message:
        Human-readable summary — never contains secrets.
    error:
        The exception that caused an unhealthy result, if any.
    """

    healthy: bool
    state: ConnectionState
    message: str
    error: Optional[Exception] = None

    @classmethod
    def from_connection_result(cls, result: ConnectionResult) -> "HealthResult":
        """Build a HealthResult from a successful ConnectionResult."""
        return cls(
            healthy=result.ready,
            state=result.state,
            message=result.message,
        )

    @classmethod
    def from_error(cls, exc: Exception) -> "HealthResult":
        """Build an unhealthy HealthResult from an exception."""
        return cls(
            healthy=False,
            state=ConnectionState.FAILED,
            message=str(exc),
            error=exc,
        )

    def __repr__(self) -> str:
        return (
            f"HealthResult(healthy={self.healthy}, state={self.state.value!r})"
        )


def check_health(manager: object) -> HealthResult:
    """Perform a health check via *manager* without raising.

    Parameters
    ----------
    manager:
        A :class:`~tekjuice_connector.connection.manager.ConnectionManager`
        instance (typed as ``object`` to avoid a circular import).

    Returns
    -------
    HealthResult
        Always returns — exceptions are caught and wrapped.
    """
    try:
        result: ConnectionResult = manager.connect()  # type: ignore[union-attr]
        return HealthResult.from_connection_result(result)
    except TekJuiceConnectorError as exc:
        return HealthResult.from_error(exc)
    except Exception as exc:
        return HealthResult.from_error(
            RuntimeError(f"Unexpected error during health check: {exc}")
        )
