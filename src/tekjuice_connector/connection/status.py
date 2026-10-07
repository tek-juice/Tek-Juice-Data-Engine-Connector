"""Connection status helpers.

Provides lightweight wrappers around :class:`ConnectionState` for
consumers that need to reason about state without importing the full
connection model.
"""

from __future__ import annotations

from tekjuice_connector.models.connection import ConnectionResult, ConnectionState


def is_ready(result: ConnectionResult) -> bool:
    """Return ``True`` if the connection is in the READY state."""
    return result.state is ConnectionState.READY and result.ready


def is_failed(result: ConnectionResult) -> bool:
    """Return ``True`` if the connection is in the FAILED state."""
    return result.state is ConnectionState.FAILED


def describe(result: ConnectionResult) -> str:
    """Return a human-readable, secret-free description of *result*."""
    parts = [f"State: {result.state.value}"]
    if result.onboarding_status:
        parts.append(f"Onboarding: {result.onboarding_status}")
    if result.domain:
        parts.append(f"Domain: {result.domain}")
    if result.message:
        parts.append(result.message)
    return " | ".join(parts)
