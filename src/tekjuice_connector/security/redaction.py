"""Secret redaction utilities.

This module provides a central sanitization layer that is applied before
any value is logged, included in a diagnostic report, or surfaced through
a public API.  All functions are pure and side-effect free so they can be
unit-tested independently.
"""

from __future__ import annotations

import re
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Redaction marker
# ---------------------------------------------------------------------------

REDACTED_MARKER = "<redacted>"

# ---------------------------------------------------------------------------
# Header names whose values should always be redacted
# ---------------------------------------------------------------------------

_SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "x-tek-juice-connector-key",
        "authorization",
        "x-api-key",
        "x-auth-token",
        "cookie",
        "set-cookie",
    }
)

# ---------------------------------------------------------------------------
# Environment / config key names whose values should always be redacted
# ---------------------------------------------------------------------------

_SENSITIVE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "tekjuice_connector_key",
        "connector_key",
        "api_key",
        "secret",
        "password",
        "token",
    }
)

# Regex pattern for connector-key-like values (long alphanumeric tokens).
# Matches strings that look like secrets: 20+ chars of hex/base64-ish content.
_SECRET_PATTERN = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def redact_header_value(name: str, value: str) -> str:
    """Return ``REDACTED_MARKER`` if ``name`` is a sensitive header name.

    Header name matching is case-insensitive.
    """
    if name.lower() in _SENSITIVE_HEADERS:
        return REDACTED_MARKER
    return value


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of *headers* with sensitive values replaced.

    Safe to call on any dict-like mapping of header name → value.
    """
    return {k: redact_header_value(k, v) for k, v in headers.items()}


def redact_env_value(key: str, value: Any) -> Any:
    """Return ``REDACTED_MARKER`` if *key* matches a sensitive env-var name.

    Matching is case-insensitive and substring-based so that e.g.
    ``TEKJUICE_CONNECTOR_KEY`` is caught by the ``connector_key`` entry.
    """
    lower = key.lower()
    for sensitive in _SENSITIVE_ENV_KEYS:
        if sensitive in lower:
            return REDACTED_MARKER
    return value


def redact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of *data* with all sensitive values redacted.

    Applies :func:`redact_env_value` to every key/value pair.
    """
    return {k: redact_env_value(k, v) for k, v in data.items()}


def redact_string(value: str) -> str:
    """Replace any embedded secret-looking token in *value* with a hint.

    Useful for scrubbing free-form text such as exception messages or log
    lines that might inadvertently contain a credential.

    The replacement leaves the first four characters intact and appends
    ``...`` so the caller can still recognise which credential was present
    without exposing the full value.
    """
    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        token = match.group(0)
        if len(token) >= 20:
            return token[:4] + "..." + REDACTED_MARKER
        return token

    return _SECRET_PATTERN.sub(_replace, value)


def safe_url(url: str) -> str:
    """Strip any embedded credentials from a URL.

    Handles ``scheme://user:password@host/path`` style URLs by replacing
    the userinfo component with ``***``.
    """
    from urllib.parse import urlparse, urlunparse

    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            # Rebuild without credentials
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            sanitized = parsed._replace(netloc=netloc)
            return urlunparse(sanitized)
    except Exception:
        pass
    return url
