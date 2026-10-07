"""Base exception for all Tek Juice Connector errors."""

from __future__ import annotations


class TekJuiceConnectorError(Exception):
    """Root exception for the Tek Juice Connector package.

    All package-specific exceptions inherit from this class, making it
    straightforward for callers to catch any Connector error with a single
    ``except TekJuiceConnectorError`` clause.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message
