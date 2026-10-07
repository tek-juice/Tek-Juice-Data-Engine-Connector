"""Package-level logger configuration.

The Tek Juice Connector uses Python's standard :mod:`logging` module.
This module:

1. Provides :func:`get_logger` — a thin wrapper that returns a logger
   scoped under the ``tekjuice_connector`` namespace.
2. Provides :func:`configure_logging` — an optional helper that sets up a
   sensible console handler for development / CLI use.  Production
   applications are expected to configure logging themselves.
3. Ensures the :class:`~tekjuice_connector.logging.sanitization.SanitizingFilter`
   is always attached to the package logger so secrets never reach handlers
   regardless of how the application configures logging.

The Connector never installs a ``NullHandler`` that silences all output;
instead it defers to the application's logging configuration while ensuring
its own output is clean.

Rules
-----
- Never log the connector key.
- Never log authorization header values.
- Never log raw exception tracebacks that contain credential strings.
- All sensitive values pass through the sanitizing filter before output.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from tekjuice_connector.logging.sanitization import SanitizingFilter, apply_to_package_logger

_PACKAGE_LOGGER_NAME = "tekjuice_connector"

# Ensure the sanitizing filter is attached immediately when this module loads.
apply_to_package_logger()


def get_logger(name: str) -> logging.Logger:
    """Return a logger in the ``tekjuice_connector`` namespace.

    Parameters
    ----------
    name:
        Typically ``__name__`` of the calling module, e.g.
        ``tekjuice_connector.authentication.handshake``.

    Returns
    -------
    logging.Logger
        A child of the package root logger, which already has the
        sanitizing filter attached.
    """
    return logging.getLogger(name)


def configure_logging(
    level: int = logging.INFO,
    *,
    stream: Optional[object] = None,
    fmt: str = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt: str = "%Y-%m-%dT%H:%M:%S",
) -> None:
    """Configure a simple console handler on the package logger.

    Intended for CLI tools and development use.  Production applications
    should configure their own logging handlers rather than calling this.

    Parameters
    ----------
    level:
        Log level for the package logger.  Default: ``logging.INFO``.
    stream:
        Output stream.  Defaults to ``sys.stderr``.
    fmt:
        Log format string.
    datefmt:
        Date format string.
    """
    pkg_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    pkg_logger.setLevel(level)

    # Avoid adding duplicate handlers if called more than once.
    for handler in pkg_logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            return

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    handler.addFilter(SanitizingFilter())
    pkg_logger.addHandler(handler)
