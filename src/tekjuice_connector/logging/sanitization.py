"""Log record sanitization.

Provides a :class:`SanitizingFilter` that scrubs sensitive values from
log records before they reach any handler.  Because this is a standard
:class:`logging.Filter`, it works with any handler (stream, file, JSON,
third-party sinks) without any special integration.

Sensitive patterns caught
-------------------------
- Any ``LogRecord`` attribute whose name matches a known sensitive key
  (connector_key, authorization, …) has its value replaced with
  ``<redacted>``.
- The ``msg`` and formatted ``message`` fields are passed through
  :func:`~tekjuice_connector.security.redaction.redact_string` to catch
  any accidental embedding of long token-like values.
- ``args`` tuples are sanitized element-by-element.

This module is intentionally dependency-free within the package so it can
be imported early without risk of circular imports.
"""

from __future__ import annotations

import logging
from typing import Any

from tekjuice_connector.security.redaction import (
    REDACTED_MARKER,
    redact_env_value,
    redact_string,
)

# Attribute names on a LogRecord that might hold sensitive data directly.
_SENSITIVE_RECORD_ATTRS = frozenset(
    {
        "connector_key",
        "api_key",
        "authorization",
        "token",
        "secret",
        "password",
    }
)


def _sanitize_value(value: Any) -> Any:
    """Sanitize a single log value."""
    if isinstance(value, str):
        return redact_string(value)
    return value


def _sanitize_args(args: Any) -> Any:
    """Sanitize the ``args`` attribute of a :class:`logging.LogRecord`."""
    if isinstance(args, tuple):
        return tuple(_sanitize_value(a) for a in args)
    if isinstance(args, dict):
        return {k: _sanitize_value(v) for k, v in args.items()}
    return args


class SanitizingFilter(logging.Filter):
    """A :class:`logging.Filter` that redacts secrets from log records.

    Attach this filter to any handler or logger::

        import logging
        from tekjuice_connector.logging.sanitization import SanitizingFilter

        handler = logging.StreamHandler()
        handler.addFilter(SanitizingFilter())

    Or apply it package-wide via :func:`apply_to_package_logger`.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        # Sanitize the message template and its arguments.
        if isinstance(record.msg, str):
            record.msg = redact_string(record.msg)
        record.args = _sanitize_args(record.args)

        # Sanitize any extra attributes that might carry sensitive data.
        for attr in _SENSITIVE_RECORD_ATTRS:
            if hasattr(record, attr):
                setattr(record, attr, REDACTED_MARKER)

        # Allow the record to proceed to the handler.
        return True


def sanitize_message(message: str) -> str:
    """Sanitize a free-form log message string.

    Convenience wrapper around
    :func:`~tekjuice_connector.security.redaction.redact_string` that
    callers can use before passing a string to a logger.

    Parameters
    ----------
    message:
        Raw log message that may contain embedded secrets.

    Returns
    -------
    str
        The message with any secret-looking tokens replaced.
    """
    return redact_string(message)


def apply_to_package_logger() -> None:
    """Attach a :class:`SanitizingFilter` to the root ``tekjuice_connector`` logger.

    Call this once during application startup to ensure all package log
    output is sanitized, regardless of which handler is configured.
    """
    pkg_logger = logging.getLogger("tekjuice_connector")
    # Avoid duplicate filters if called more than once.
    for f in pkg_logger.filters:
        if isinstance(f, SanitizingFilter):
            return
    pkg_logger.addFilter(SanitizingFilter())
