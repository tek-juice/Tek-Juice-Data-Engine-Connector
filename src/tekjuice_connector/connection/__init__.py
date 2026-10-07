"""Public re-exports for the connection subpackage."""

from tekjuice_connector.connection.health import HealthResult, check_health
from tekjuice_connector.connection.lifecycle import (
    build_result_from_session,
    derive_state,
    is_valid_transition,
)
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.connection.status import describe, is_failed, is_ready

__all__ = [
    "ConnectionManager",
    "HealthResult",
    "check_health",
    "build_result_from_session",
    "derive_state",
    "is_valid_transition",
    "describe",
    "is_failed",
    "is_ready",
]
