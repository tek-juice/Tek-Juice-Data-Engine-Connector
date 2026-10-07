"""Configuration validation rules.

All validation is pure — it raises typed exceptions rather than returning
booleans.  Nothing in this module performs network I/O.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from tekjuice_connector.exceptions.configuration import MissingConfigurationError
from tekjuice_connector.exceptions.validation import ValidationError
from tekjuice_connector.models.common import (
    ENV_CONNECTOR_KEY,
    ENV_DATA_ENGINE_URL,
    ENV_WEBSITE_ID,
)

# Website ID: non-empty string, max 255 chars, printable ASCII with limited
# special characters (letters, digits, hyphens, underscores, dots).
_WEBSITE_ID_RE = re.compile(r"^[\w.\-]{1,255}$")

_ALLOWED_URL_SCHEMES = {"https", "http"}


def validate_website_id(value: Optional[str]) -> str:
    """Validate and return the website_id.

    Raises
    ------
    MissingConfigurationError
        When ``value`` is ``None`` or empty.
    ValidationError
        When the value contains disallowed characters or is too long.
    """
    if not value:
        raise MissingConfigurationError(ENV_WEBSITE_ID)
    value = value.strip()
    if not _WEBSITE_ID_RE.match(value):
        raise ValidationError(
            f"TEKJUICE_WEBSITE_ID contains invalid characters or is too long. "
            "Allowed: letters, digits, hyphens, underscores, dots (max 255 chars).",
            field="website_id",
        )
    return value


def validate_connector_key(value: Optional[str]) -> str:
    """Validate and return the connector_key.

    Raises
    ------
    MissingConfigurationError
        When ``value`` is ``None`` or empty.

    The raw key is never included in exception messages.
    """
    if not value or not value.strip():
        raise MissingConfigurationError(ENV_CONNECTOR_KEY)
    return value.strip()


def validate_data_engine_url(value: Optional[str]) -> str:
    """Validate and return the data_engine_url.

    Raises
    ------
    MissingConfigurationError
        When ``value`` is ``None`` or empty.
    ValidationError
        When the URL is not a valid http/https URL.
    """
    if not value or not value.strip():
        raise MissingConfigurationError(ENV_DATA_ENGINE_URL)

    url = value.strip().rstrip("/")

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ValidationError(
            f"TEKJUICE_DATA_ENGINE_URL is not a valid URL: {exc}",
            field="data_engine_url",
        ) from exc

    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValidationError(
            f"TEKJUICE_DATA_ENGINE_URL must use http or https scheme, "
            f"got '{parsed.scheme}'.",
            field="data_engine_url",
        )

    if not parsed.netloc:
        raise ValidationError(
            "TEKJUICE_DATA_ENGINE_URL must include a hostname.",
            field="data_engine_url",
        )

    if parsed.scheme == "http":
        # Warn-level only — enforced in security layer; here we just validate.
        pass

    return url
