"""Public re-exports for the exceptions subpackage."""

from tekjuice_connector.exceptions.api import ApiError, MalformedResponseError, ServerError
from tekjuice_connector.exceptions.authentication import (
    AuthenticationError,
    InvalidCredentialsError,
    WebsiteIdMismatchError,
)
from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.exceptions.configuration import (
    ConfigurationError,
    MissingConfigurationError,
)
from tekjuice_connector.exceptions.connection import (
    ConnectionError,
    ConnectionTimeoutError,
    RetryExhaustedError,
)
from tekjuice_connector.exceptions.validation import ResponseValidationError, ValidationError

__all__ = [
    "TekJuiceConnectorError",
    "ConfigurationError",
    "MissingConfigurationError",
    "ValidationError",
    "ResponseValidationError",
    "AuthenticationError",
    "InvalidCredentialsError",
    "WebsiteIdMismatchError",
    "ConnectionError",
    "ConnectionTimeoutError",
    "RetryExhaustedError",
    "ApiError",
    "ServerError",
    "MalformedResponseError",
]
