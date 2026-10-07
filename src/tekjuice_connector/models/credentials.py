"""Credential model — never exposes the raw connector key in repr()."""

from __future__ import annotations

from dataclasses import dataclass


_REDACTED = "<redacted>"


@dataclass
class ConnectorCredentials:
    """Holds the three pieces of Connector configuration.

    The ``connector_key`` is intentionally hidden from ``__repr__`` and
    ``__str__`` so it never leaks into logs, tracebacks or diagnostic output.
    """

    website_id: str
    connector_key: str
    data_engine_url: str

    # ------------------------------------------------------------------
    # Safety: never expose the connector key in any string representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"ConnectorCredentials("
            f"website_id={self.website_id!r}, "
            f"connector_key={_REDACTED}, "
            f"data_engine_url={self.data_engine_url!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def safe_key_hint(self) -> str:
        """Return only the first four characters of the key as a hint."""
        if len(self.connector_key) >= 4:
            return self.connector_key[:4] + "..."
        return _REDACTED
