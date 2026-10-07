"""Connection state and result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from tekjuice_connector.models.authentication import HandshakeResponse


class ConnectionState(str, Enum):
    """Connector-side connection state machine.

    These are local representations only.
    The Data Engine is authoritative for actual onboarding state.
    """

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATED = "AUTHENTICATED"
    BACKEND_CONNECTED = "BACKEND_CONNECTED"
    READY = "READY"
    FAILED = "FAILED"


@dataclass
class ConnectionResult:
    """The result returned by ``TekJuiceConnector.connect()``.

    Preserves the Data Engine's authoritative response fields verbatim.
    """

    state: ConnectionState
    ready: bool

    # Data Engine authoritative fields
    website_id: Optional[str] = None
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    onboarding_status: Optional[str] = None
    connector_authenticated: Optional[bool] = None
    backend_connected: Optional[bool] = None

    # Human-readable message (never contains secrets)
    message: str = ""

    @classmethod
    def from_handshake(cls, response: HandshakeResponse) -> "ConnectionResult":
        """Build a ConnectionResult from a successful HandshakeResponse."""
        state = ConnectionState.READY if response.ready else ConnectionState.AUTHENTICATED
        return cls(
            state=state,
            ready=response.ready,
            website_id=response.website_id,
            tenant_id=response.tenant_id,
            domain=response.domain,
            onboarding_status=response.onboarding_status,
            connector_authenticated=response.connector_authenticated,
            backend_connected=response.backend_connected,
            message=(
                "Connector authenticated and backend connected. READY."
                if response.ready
                else f"Connector authenticated. Onboarding status: {response.onboarding_status}"
            ),
        )

    @classmethod
    def failed(cls, message: str) -> "ConnectionResult":
        """Build a failed ConnectionResult."""
        return cls(state=ConnectionState.FAILED, ready=False, message=message)

    def __repr__(self) -> str:
        return (
            f"ConnectionResult("
            f"state={self.state.value!r}, "
            f"ready={self.ready}, "
            f"onboarding_status={self.onboarding_status!r})"
        )
