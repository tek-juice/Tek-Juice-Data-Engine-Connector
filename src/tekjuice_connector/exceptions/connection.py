"""Connection errors."""

from __future__ import annotations

from tekjuice_connector.exceptions.base import TekJuiceConnectorError


class ConnectionError(TekJuiceConnectorError):
    """Raised when a network connection to the Data Engine cannot be established
    or is lost during the operation."""


class ConnectionTimeoutError(ConnectionError):
    """Raised when the Data Engine does not respond within the configured timeout."""

    def __init__(self, url: str, timeout: float) -> None:
        super().__init__(
            f"Connection to '{url}' timed out after {timeout:.1f}s. "
            "Check TEKJUICE_DATA_ENGINE_URL and network connectivity."
        )
        self.url = url
        self.timeout = timeout


class RetryExhaustedError(ConnectionError):
    """Raised when all retry attempts have been exhausted without success."""

    def __init__(self, attempts: int, last_message: str) -> None:
        super().__init__(
            f"Operation failed after {attempts} attempt(s). Last error: {last_message}"
        )
        self.attempts = attempts
        self.last_message = last_message
