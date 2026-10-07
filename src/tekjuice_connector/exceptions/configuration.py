"""Configuration and environment variable errors."""

from __future__ import annotations

from typing import Optional

from tekjuice_connector.exceptions.base import TekJuiceConnectorError


class ConfigurationError(TekJuiceConnectorError):
    """Raised when required configuration is missing or unusable.

    Examples: missing environment variable, missing explicit argument.
    The connector key is never included in the message.
    """

    def __init__(self, message: str, missing_variable: Optional[str] = None) -> None:
        super().__init__(message)
        self.missing_variable = missing_variable


class MissingConfigurationError(ConfigurationError):
    """Raised when a required configuration variable is absent."""

    def __init__(self, variable_name: str) -> None:
        super().__init__(
            f"Required configuration variable '{variable_name}' is not set.",
            missing_variable=variable_name,
        )
        self.variable_name = variable_name
