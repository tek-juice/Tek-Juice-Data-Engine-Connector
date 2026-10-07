"""HTTP header construction for Connector requests.

The connector key is handled here and only here.  No other module in the
package constructs authorization headers, ensuring the key never leaks
through logging or exception paths that touch request/response objects.
"""

from __future__ import annotations

from typing import Dict

from tekjuice_connector.models.common import CONNECTOR_KEY_HEADER
from tekjuice_connector.version import __version__

_USER_AGENT = f"tekjuice-connector-python/{__version__}"


def build_request_headers(connector_key: str) -> Dict[str, str]:
    """Return the full set of headers required for a Connector API request.

    Parameters
    ----------
    connector_key:
        The raw connector key value.  It is placed in the header map but
        is **not** logged or surfaced anywhere in this function.

    Returns
    -------
    dict
        Ready-to-use headers dict.  The caller should treat this dict as
        opaque — never log it directly.
    """
    return {
        CONNECTOR_KEY_HEADER: connector_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }


def safe_headers_for_logging(headers: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of *headers* safe to log — connector key is redacted."""
    from tekjuice_connector.security.redaction import redact_headers

    return redact_headers(headers)
