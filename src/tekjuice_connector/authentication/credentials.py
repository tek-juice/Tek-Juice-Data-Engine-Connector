"""Credential loading and validation for the authentication subsystem.

This module is the bridge between the config layer and the authentication
layer.  It produces a validated :class:`ConnectorCredentials` object that
the rest of authentication uses.
"""

from __future__ import annotations

from tekjuice_connector.config.settings import ConnectorSettings, load_settings
from tekjuice_connector.models.credentials import ConnectorCredentials


def load_credentials(
    *,
    website_id: str | None = None,
    connector_key: str | None = None,
    data_engine_url: str | None = None,
    load_dotenv_file: bool = True,
) -> ConnectorCredentials:
    """Load, validate, and return :class:`ConnectorCredentials`.

    Thin wrapper around :func:`~tekjuice_connector.config.settings.load_settings`
    that returns only the credentials portion.  All validation logic lives
    in the config layer.

    Parameters
    ----------
    website_id, connector_key, data_engine_url:
        Explicit values that override environment variables.
    load_dotenv_file:
        Passed through to the environment loader.

    Returns
    -------
    ConnectorCredentials
        Validated, ready-to-use credentials.

    Raises
    ------
    MissingConfigurationError
        When a required variable is absent.
    ValidationError
        When a provided value fails format validation.
    """
    settings: ConnectorSettings = load_settings(
        website_id=website_id,
        connector_key=connector_key,
        data_engine_url=data_engine_url,
        load_dotenv_file=load_dotenv_file,
    )
    return settings.credentials
