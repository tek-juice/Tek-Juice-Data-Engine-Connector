"""Error detail models used by exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HttpErrorDetail:
    """Safe HTTP error information — never contains secret headers or keys."""

    status_code: int
    endpoint: str
    method: str = "POST"
    message: str = ""
    response_body_summary: Optional[str] = None

    def __str__(self) -> str:
        return f"HTTP {self.status_code} {self.method} {self.endpoint}: {self.message}"


@dataclass(frozen=True)
class ValidationErrorDetail:
    """Details about a configuration or response validation failure."""

    field: str
    issue: str
    value_hint: Optional[str] = None  # Safe summary, never a raw secret

    def __str__(self) -> str:
        hint = f" (got: {self.value_hint})" if self.value_hint else ""
        return f"Validation failed for '{self.field}': {self.issue}{hint}"
