"""Common API response structures and shared typing utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Protocol constants (canonical location — imported everywhere else from here)
# ---------------------------------------------------------------------------

HANDSHAKE_ENDPOINT = "/api/v1/connector/handshake"
CONNECTOR_KEY_HEADER = "X-Tek-Juice-Connector-Key"

ENV_WEBSITE_ID = "TEKJUICE_WEBSITE_ID"
ENV_CONNECTOR_KEY = "TEKJUICE_CONNECTOR_KEY"
ENV_DATA_ENGINE_URL = "TEKJUICE_DATA_ENGINE_URL"

DEFAULT_CONNECT_TIMEOUT = 10.0   # seconds
DEFAULT_READ_TIMEOUT = 30.0      # seconds
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0       # seconds


# ---------------------------------------------------------------------------
# Generic API error payload
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ApiErrorDetail:
    """Structured representation of a Data Engine error response body."""

    status_code: int
    endpoint: str
    message: str
    raw_body: Optional[str] = None

    def __str__(self) -> str:
        return f"HTTP {self.status_code} from {self.endpoint}: {self.message}"


# ---------------------------------------------------------------------------
# Onboarding status enumeration (mirrors Data Engine values)
# ---------------------------------------------------------------------------

class OnboardingStatus:
    """Known onboarding status strings returned by the Data Engine."""

    READY = "READY"
    PENDING = "PENDING"
    FAILED = "FAILED"

    _known: tuple = ("READY", "PENDING", "FAILED")

    @classmethod
    def is_known(cls, value: str) -> bool:
        return value in cls._known
