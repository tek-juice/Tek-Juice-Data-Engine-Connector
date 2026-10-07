"""Typed models for the handshake request and response."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HandshakeRequest:
    """Payload sent to the Data Engine handshake endpoint."""

    website_id: str

    def to_dict(self) -> dict:
        return {"website_id": self.website_id}


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
        )
