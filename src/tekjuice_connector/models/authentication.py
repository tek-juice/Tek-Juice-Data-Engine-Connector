"""Typed models for the handshake request and response."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class HandshakeRequest:
    """Payload sent to the Data Engine handshake endpoint.

    ``publication`` optionally registers this backend's publication receiver
    (see :class:`~tekjuice_connector.publication.PublicationRegistration`).
    It contains the receiver secret, so this object must never be logged.
    """

    website_id: str
    publication: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        body: Dict[str, Any] = {"website_id": self.website_id}
        if self.publication is not None:
            body["publication"] = self.publication
        return body

    def __repr__(self) -> str:
        return (
            f"HandshakeRequest(website_id={self.website_id!r}, "
            f"publication={'<registered>' if self.publication else None})"
        )


@dataclass(frozen=True)
class HandshakeResponse:
    """Validated response from the Data Engine handshake endpoint.

    All fields reflect what the Data Engine actually returned.
    The Connector does not invent or override any of these values.
    """

    website_id: str
    tenant_id: str
    domain: str
    onboarding_status: str
    connector_authenticated: bool
    backend_connected: bool
    ready: bool
    # Present when the Data Engine supports publication readiness (>= the
    # S2S publication release); None against older Data Engines.
    publication_status: Optional[str] = None
    publication_ready: Optional[bool] = None
    next_action: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "HandshakeResponse":
        """Construct from a raw response dictionary.

        Raises ``KeyError`` if required fields are absent.
        """
        return cls(
            website_id=data["website_id"],
            tenant_id=data["tenant_id"],
            domain=data["domain"],
            onboarding_status=data["onboarding_status"],
            connector_authenticated=bool(data["connector_authenticated"]),
            backend_connected=bool(data["backend_connected"]),
            ready=bool(data["ready"]),
            publication_status=data.get("publication_status"),
            publication_ready=data.get("publication_ready"),
            next_action=data.get("next_action"),
        )
