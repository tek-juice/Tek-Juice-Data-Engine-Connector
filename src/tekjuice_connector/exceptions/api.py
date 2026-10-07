"""HTTP / API-level errors."""

from __future__ import annotations

from typing import Optional

from tekjuice_connector.exceptions.base import TekJuiceConnectorError


class ApiError(TekJuiceConnectorError):
    """Raised for HTTP errors returned by the Data Engine.

    Preserves safe information (status code, endpoint) but never leaks
    authorization headers or connector keys.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        endpoint: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint


class ServerError(ApiError):
    """Raised for 5xx responses — these may be transient and are retryable."""

    def __init__(self, status_code: int, endpoint: str) -> None:
        super().__init__(
            f"Data Engine returned server error HTTP {status_code} from {endpoint}.",
            status_code=status_code,
            endpoint=endpoint,
        )


class MalformedResponseError(ApiError):
    """Raised when the Data Engine returns a response that cannot be parsed."""

    def __init__(self, endpoint: str, detail: str) -> None:
        super().__init__(
            f"Malformed response from {endpoint}: {detail}",
            endpoint=endpoint,
        )
