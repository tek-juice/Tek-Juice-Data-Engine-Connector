"""Connection lifecycle transitions.

Defines the valid state-machine transitions for the Connector-side
connection state.  The Data Engine is authoritative for actual onboarding
state; these transitions reflect what the Connector *observes*.

Transition rules
----------------
DISCONNECTED → CONNECTING
CONNECTING   → AUTHENTICATED  (connector_authenticated is True)
AUTHENTICATED → BACKEND_CONNECTED (backend_connected is True)
BACKEND_CONNECTED → READY    (ready is True)
Any state    → FAILED         (on error)
FAILED       → CONNECTING     (on retry / reconnect)
"""

from __future__ import annotations

from tekjuice_connector.authentication.session import AuthSession
from tekjuice_connector.models.connection import ConnectionResult, ConnectionState


# ---------------------------------------------------------------------------
# State derivation from an AuthSession
# ---------------------------------------------------------------------------


def derive_state(session: AuthSession) -> ConnectionState:
    """Derive the appropriate :class:`ConnectionState` from an *AuthSession*.

    Follows the Data Engine's authoritative fields in order of precedence:
    ready > backend_connected > connector_authenticated.
    """
    if session.ready:
        return ConnectionState.READY
    if session.backend_connected:
        return ConnectionState.BACKEND_CONNECTED
    if session.connector_authenticated:
        return ConnectionState.AUTHENTICATED
    return ConnectionState.CONNECTING


def build_result_from_session(session: AuthSession) -> ConnectionResult:
    """Build a :class:`ConnectionResult` from a completed :class:`AuthSession`."""
    return ConnectionResult.from_handshake(session.response)


# ---------------------------------------------------------------------------
# Transition validation (defensive, not enforced at runtime by default)
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[ConnectionState, set[ConnectionState]] = {
    ConnectionState.DISCONNECTED: {ConnectionState.CONNECTING, ConnectionState.FAILED},
    ConnectionState.CONNECTING: {
        ConnectionState.AUTHENTICATED,
        ConnectionState.BACKEND_CONNECTED,
        ConnectionState.READY,
        ConnectionState.FAILED,
    },
    ConnectionState.AUTHENTICATED: {
        ConnectionState.BACKEND_CONNECTED,
        ConnectionState.READY,
        ConnectionState.FAILED,
        ConnectionState.CONNECTING,  # reconnect allowed
    },
    ConnectionState.BACKEND_CONNECTED: {
        ConnectionState.READY,
        ConnectionState.FAILED,
        ConnectionState.CONNECTING,
    },
    ConnectionState.READY: {
        ConnectionState.CONNECTING,  # reconnect / redeploy
        ConnectionState.FAILED,
    },
    ConnectionState.FAILED: {
        ConnectionState.CONNECTING,
    },
}


def is_valid_transition(from_state: ConnectionState, to_state: ConnectionState) -> bool:
    """Return ``True`` if moving from *from_state* to *to_state* is permitted."""
    return to_state in _VALID_TRANSITIONS.get(from_state, set())
