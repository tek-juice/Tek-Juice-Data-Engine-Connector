"""Timeout configuration for HTTP requests."""

from __future__ import annotations

from dataclasses import dataclass

from tekjuice_connector.models.common import DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT


@dataclass(frozen=True)
class TimeoutConfig:
    """Pair of connect and read timeouts used for every HTTP request.

    Attributes
    ----------
    connect:
        Seconds to wait for the TCP handshake to complete.
    read:
        Seconds to wait for the server to begin sending the response body.
    """

    connect: float = DEFAULT_CONNECT_TIMEOUT
    read: float = DEFAULT_READ_TIMEOUT

    def as_tuple(self) -> tuple[float, float]:
        """Return ``(connect, read)`` — matches the ``requests`` timeout arg."""
        return (self.connect, self.read)

    def __repr__(self) -> str:
        return f"TimeoutConfig(connect={self.connect}s, read={self.read}s)"
