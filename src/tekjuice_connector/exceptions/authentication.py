"""Authentication errors."""

from __future__ import annotations

from typing import Optional

from tekjuice_connector.exceptions.base import TekJuiceConnectorError


class AuthenticationError(TekJuiceConnectorError):
    """Raised when authentication with the Data Engine fails.

    This covers invalid credentials, rejected connector keys, and other
    permanent authentication failures.  The connector key is never included
    in the message.
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class InvalidCredentialsError(AuthenticationError):
    """Raised when the Data Engine rejects the connector key (HTTP 401/403)."""

    def __init__(self, status_code: int = 401) -> None:
        super().__init__(
            f"Connector key was rejected by the Data Engine (HTTP {status_code}). "
            "Check TEKJUICE_CONNECTOR_KEY.",
            status_code=status_code,
        )


class WebsiteIdMismatchError(AuthenticationError):
    """Raised when the response website_id does not match the configured one."""

    def __init__(self, configured: str, received: str) -> None:
        super().__init__(
            f"Response website_id '{received}' does not match configured "
            f"website_id '{configured}'. Possible misconfiguration."
        )
        self.configured = configured
        self.received = received
