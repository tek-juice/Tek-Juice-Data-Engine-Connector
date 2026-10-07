"""Typed settings object.

This is the single place that combines environment loading, explicit
overrides and validation into a ready-to-use ``ConnectorSettings`` object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tekjuice_connector.config.environment import load_env
from tekjuice_connector.config.validation import (
    validate_connector_key,
    validate_data_engine_url,
    validate_website_id,
)
from tekjuice_connector.models.common import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_READ_TIMEOUT,
)
from tekjuice_connector.models.credentials import ConnectorCredentials


@dataclass
class ConnectorSettings:
    """Fully validated Connector settings.

    Instantiate via :func:`load_settings` rather than directly so that
    validation is always applied.

    Attributes
    ----------
    credentials:
        Validated website ID, connector key and Data Engine URL.
    connect_timeout:
        Seconds to wait for a TCP connection to be established.
    read_timeout:
        Seconds to wait for the server to send a response.
    max_retries:
        Maximum number of retry attempts for transient failures.
    backoff_base:
        Base delay in seconds for the exponential backoff calculation.
    """

    credentials: ConnectorCredentials
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base: float = DEFAULT_BACKOFF_BASE

    def __repr__(self) -> str:
        # Delegate to credentials repr, which redacts the key.
        return (
            f"ConnectorSettings("
            f"credentials={self.credentials!r}, "
            f"connect_timeout={self.connect_timeout}, "
            f"read_timeout={self.read_timeout}, "
            f"max_retries={self.max_retries})"
        )


def load_settings(
    *,
    website_id: Optional[str] = None,
    connector_key: Optional[str] = None,
    data_engine_url: Optional[str] = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    load_dotenv_file: bool = True,
) -> ConnectorSettings:
    """Build and validate a :class:`ConnectorSettings` instance.

    Explicit keyword arguments take precedence over environment variables.
    Environment variables are loaded from the process environment (and
    optionally a .env file) for any argument that is ``None``.

    Parameters
    ----------
    website_id, connector_key, data_engine_url:
        Explicit values that override environment variables when provided.
    connect_timeout, read_timeout:
        HTTP timeout values in seconds.
    max_retries:
        Maximum retry attempts for transient failures.
    backoff_base:
        Base delay for exponential backoff.
    load_dotenv_file:
        Pass ``False`` to skip .env file loading (useful in tests).

    Returns
    -------
    ConnectorSettings
        Validated settings ready for use.

    Raises
    ------
    MissingConfigurationError
        When a required variable is absent from both explicit args and env.
    ValidationError
        When a provided value fails format validation.
    """
    env = load_env(load_dotenv_file=load_dotenv_file)

    resolved_website_id = website_id or env["website_id"]
    resolved_connector_key = connector_key or env["connector_key"]
    resolved_data_engine_url = data_engine_url or env["data_engine_url"]

    credentials = ConnectorCredentials(
        website_id=validate_website_id(resolved_website_id),
        connector_key=validate_connector_key(resolved_connector_key),
        data_engine_url=validate_data_engine_url(resolved_data_engine_url),
    )

    return ConnectorSettings(
        credentials=credentials,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        max_retries=max_retries,
        backoff_base=backoff_base,
    )
