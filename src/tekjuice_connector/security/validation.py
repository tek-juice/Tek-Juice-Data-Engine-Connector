"""Security-oriented validation checks.

These go beyond the syntactic checks in ``config/validation.py`` and
enforce security policies such as the HTTPS requirement for production.
"""

from __future__ import annotations

import warnings
from urllib.parse import urlparse

from tekjuice_connector.exceptions.validation import ValidationError


def assert_https(url: str, *, allow_http: bool = False) -> None:
    """Warn (or raise) when a non-HTTPS URL is used.

    Parameters
    ----------
    url:
        The Data Engine URL to check.
    allow_http:
        When ``True`` a plain HTTP URL only triggers a :class:`UserWarning`.
        When ``False`` (production default) a :class:`ValidationError` is
        raised so the configuration cannot be used silently.

    Raises
    ------
    ValidationError
        If the URL uses HTTP and ``allow_http`` is ``False``.
    """
    parsed = urlparse(url)
    if parsed.scheme == "http":
        msg = (
            f"TEKJUICE_DATA_ENGINE_URL uses plain HTTP ('{url}'). "
            "This transmits the connector key over an unencrypted connection. "
            "Use HTTPS in production."
        )
        if allow_http:
            warnings.warn(msg, UserWarning, stacklevel=3)
        else:
            raise ValidationError(msg, field="data_engine_url")


def assert_no_credentials_in_url(url: str) -> None:
    """Raise if the URL embeds credentials in the userinfo component.

    Raises
    ------
    ValidationError
    """
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise ValidationError(
            "TEKJUICE_DATA_ENGINE_URL must not embed credentials. "
            "Use TEKJUICE_CONNECTOR_KEY instead.",
            field="data_engine_url",
        )


def validate_url_security(url: str, *, allow_http: bool = False) -> None:
    """Run all security checks on a Data Engine URL.

    Convenience wrapper that calls both :func:`assert_https` and
    :func:`assert_no_credentials_in_url`.
    """
    assert_no_credentials_in_url(url)
    assert_https(url, allow_http=allow_http)
