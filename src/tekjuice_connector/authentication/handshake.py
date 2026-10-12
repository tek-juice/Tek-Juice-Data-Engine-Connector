"""Handshake client — executes the E2E authentication call against the Data Engine.

Contract
--------
POST /api/v1/connector/handshake
Header: X-Tek-Juice-Connector-Key: <connector_key>
Body:   {"website_id": "<website_id>"}

Success response (HTTP 200):
{
    "website_id": "...",
    "tenant_id": "...",
    "domain": "...",
    "onboarding_status": "READY",
    "connector_authenticated": true,
    "backend_connected": true,
    "ready": true
}

The Connector never modifies Data Engine state backwards.  A website already
in READY state will receive an identical READY response on repeated calls,
which is handled gracefully here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from tekjuice_connector.client.client import DataEngineClient
from tekjuice_connector.exceptions.api import ApiError
from tekjuice_connector.exceptions.authentication import WebsiteIdMismatchError
from tekjuice_connector.exceptions.validation import ResponseValidationError
from tekjuice_connector.models.authentication import HandshakeRequest, HandshakeResponse
from tekjuice_connector.models.common import HANDSHAKE_ENDPOINT


_REQUIRED_RESPONSE_FIELDS = (
    "website_id",
    "tenant_id",
    "domain",
    "onboarding_status",
    "connector_authenticated",
    "backend_connected",
    "ready",
)


def perform_handshake(
    client: DataEngineClient,
    website_id: str,
    publication: Optional[Dict[str, Any]] = None,
) -> HandshakeResponse:
    """Execute the E2E handshake and return a validated :class:`HandshakeResponse`.

    Parameters
    ----------
    client:
        An authenticated :class:`~tekjuice_connector.client.DataEngineClient`.
    website_id:
        The website ID to include in the request body and validate against
        the response.

    Returns
    -------
    HandshakeResponse
        Typed, validated response from the Data Engine.

    Raises
    ------
    ResponseValidationError
        When the response body is missing required fields or has unexpected
        types.
    WebsiteIdMismatchError
        When the response ``website_id`` does not match the configured one.
    InvalidCredentialsError
        When the Data Engine rejects the connector key (HTTP 401/403).
    ServerError
        On 5xx responses (retryable by the retry layer).
    MalformedResponseError
        When the response body is not valid JSON.
    ConnectionTimeoutError / ConnectionError
        On network-level failures.
    """
    request = HandshakeRequest(website_id=website_id, publication=publication)
    response = client.post(HANDSHAKE_ENDPOINT, payload=request.to_dict())

    if response.status_code >= 400:
        # 401/403/5xx are raised by the transport; remaining 4xx responses
        # (e.g. ownership not yet verified, invalid publication endpoint)
        # carry an actionable Data Engine message.
        raise ApiError(
            f"Data Engine rejected the handshake (HTTP {response.status_code}): "
            f"{_error_message(response.body)}",
            status_code=response.status_code,
            endpoint=HANDSHAKE_ENDPOINT,
        )

    if not isinstance(response.body, dict):
        raise ResponseValidationError(
            f"Handshake response body must be a JSON object, "
            f"got {type(response.body).__name__}.",
            field="body",
        )

    _validate_response_fields(response.body)

    handshake_response = _parse_response(response.body)

    # Verify the Data Engine echoed back the correct website_id.
    if handshake_response.website_id != website_id:
        raise WebsiteIdMismatchError(
            configured=website_id,
            received=handshake_response.website_id,
        )

    return handshake_response


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _validate_response_fields(body: dict) -> None:
    """Raise ResponseValidationError for any missing required field."""
    missing = [f for f in _REQUIRED_RESPONSE_FIELDS if f not in body]
    if missing:
        raise ResponseValidationError(
            f"Handshake response is missing required field(s): "
            f"{', '.join(missing)}.",
            field=missing[0],
        )

    # Type checks for boolean fields
    for bool_field in ("connector_authenticated", "backend_connected", "ready"):
        value = body[bool_field]
        if not isinstance(value, bool):
            raise ResponseValidationError(
                f"Handshake response field '{bool_field}' must be a boolean, "
                f"got {type(value).__name__}.",
                field=bool_field,
            )


def _error_message(body: object) -> str:
    """Extract a safe message from a Data Engine error body."""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        for key in ("message", "detail"):
            if body.get(key):
                return str(body[key])
    return "no error message provided"


def _parse_response(body: dict) -> HandshakeResponse:
    """Build a HandshakeResponse from a validated body dict."""
    try:
        return HandshakeResponse.from_dict(body)
    except (KeyError, TypeError) as exc:
        raise ResponseValidationError(
            f"Failed to parse handshake response: {exc}",
            field="body",
        ) from exc
