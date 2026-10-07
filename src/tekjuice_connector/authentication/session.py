"""Authentication session — holds the result of a completed handshake.

An ``AuthSession`` is a lightweight, immutable record of a successful
authentication exchange.  It is NOT a persistent server-side session token;
the Data Engine is stateless for the purposes of the Connector handshake.

The Connector will re-authenticate (call perform_handshake again) on each
``connect()`` invocation, so sessions do not need to be persisted across
process restarts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from tekjuice_connector.models.authentication import HandshakeResponse


@dataclass(frozen=True)
class AuthSession:
    """Record of a completed handshake.

    Attributes
    ----------
    response:
        The validated :class:`HandshakeResponse` from the Data Engine.
    authenticated_at:
        UTC timestamp of when the handshake completed.
    """

    response: HandshakeResponse
    authenticated_at: datetime

    @classmethod
    def create(cls, response: HandshakeResponse) -> "AuthSession":
        """Create a new session stamped with the current UTC time."""
        return cls(
            response=response,
            authenticated_at=datetime.now(tz=timezone.utc),
        )

    # ------------------------------------------------------------------
    # Convenience properties (mirrors HandshakeResponse for easy access)
    # ------------------------------------------------------------------

    @property
    def website_id(self) -> str:
        return self.response.website_id

    @property
    def tenant_id(self) -> str:
        return self.response.tenant_id

    @property
    def domain(self) -> str:
        return self.response.domain

    @property
    def onboarding_status(self) -> str:
        return self.response.onboarding_status

    @property
    def connector_authenticated(self) -> bool:
        return self.response.connector_authenticated

    @property
    def backend_connected(self) -> bool:
        return self.response.backend_connected

    @property
    def ready(self) -> bool:
        return self.response.ready

    def __repr__(self) -> str:
        return (
            f"AuthSession("
            f"website_id={self.website_id!r}, "
            f"ready={self.ready}, "
            f"onboarding_status={self.onboarding_status!r}, "
            f"authenticated_at={self.authenticated_at.isoformat()!r})"
        )
