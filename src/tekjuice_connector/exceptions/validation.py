"""Validation errors for configuration and API responses."""

from __future__ import annotations

from typing import Optional

from tekjuice_connector.exceptions.base import TekJuiceConnectorError


class ValidationError(TekJuiceConnectorError):
    """Raised when a value fails validation rules.

    Used for both configuration validation (bad URL, empty website ID) and
    response validation (unexpected field values, missing fields).
    """

    def __init__(self, message: str, field: Optional[str] = None) -> None:
        super().__init__(message)
        self.field = field


class ResponseValidationError(ValidationError):
    """Raised when the Data Engine returns a structurally valid HTTP response
    whose content does not satisfy the expected contract."""

    def __init__(self, message: str, field: Optional[str] = None) -> None:
        super().__init__(message, field=field)
