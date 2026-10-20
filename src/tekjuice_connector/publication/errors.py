"""Publication receiver errors.

Every error maps to one HTTP status and a stable machine-readable ``code``.
The Data Engine classifies 4xx responses as permanent contract failures
(never retried) and 5xx responses as transient (retried with backoff), so
each error's status code is chosen to give the correct retry behaviour.
"""

from __future__ import annotations

from typing import Any, Dict

from tekjuice_connector.exceptions.base import TekJuiceConnectorError


class PublicationError(TekJuiceConnectorError):
    """Base class for errors returned to the Data Engine by the receiver."""

    status_code: int = 500
    code: str = "publication_error"
    retryable: bool = False

    def to_response_body(self) -> Dict[str, Any]:
        return {
            "status": "error",
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            },
        }


class MalformedRequestError(PublicationError):
    """The request body is not a JSON object."""

    status_code = 400
    code = "malformed_request"


class ReceiverAuthenticationError(PublicationError):
    """The bearer credential is missing or does not match."""

    status_code = 401
    code = "unauthorized"


class WebsiteMismatchError(PublicationError):
    """The request targets a different website than this receiver serves."""

    status_code = 403
    code = "website_mismatch"


class TargetNotFoundError(PublicationError):
    """An update targets content this receiver does not hold."""

    status_code = 404
    code = "target_not_found"


class DuplicateSlugError(PublicationError):
    """A create uses a slug already owned by different content."""

    status_code = 409
    code = "duplicate_slug"


class RequestTooLargeError(PublicationError):
    """The request body exceeds the receiver's size limit."""

    status_code = 413
    code = "request_too_large"


class ContractViolationError(PublicationError):
    """The request does not satisfy the publication contract."""

    status_code = 422
    code = "contract_violation"


class UnsupportedContractVersionError(ContractViolationError):
    code = "unsupported_contract_version"


class UnsupportedOperationError(ContractViolationError):
    """The receiver's content store does not support the requested action."""

    code = "unsupported_operation"


class UnsupportedCapabilityError(UnsupportedOperationError):
    """The customer environment cannot perform the requested change.

    The body names the missing capability so the Data Engine can keep the
    remediation plan for a later, supported workflow.
    """

    code = "unsupported_capability"

    def __init__(self, message: str, *, capability: str) -> None:
        super().__init__(message)
        self.capability = capability

    def to_response_body(self) -> Dict[str, Any]:
        body = super().to_response_body()
        body["error"]["capability"] = self.capability
        return body


class ContentTypeUnavailableError(UnsupportedOperationError):
    """The publication names a content type the backend does not offer, or
    one that is not ready (permanent: the body says what is missing)."""

    code = "content_type_unavailable"

    def __init__(self, message: str, *, content_type: str, reasons=()) -> None:
        super().__init__(message)
        self.content_type = content_type
        self.reasons = [str(r)[:300] for r in reasons][:10]

    def to_response_body(self) -> Dict[str, Any]:
        body = super().to_response_body()
        body["error"]["content_type"] = self.content_type
        body["error"]["reasons"] = self.reasons
        return body


class ContentMappingError(ContractViolationError):
    """The content cannot be mapped to the destination schema; nothing was
    written. The body carries the mapping report."""

    code = "content_mapping_failed"

    def __init__(self, message: str, *, report: Dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report

    def to_response_body(self) -> Dict[str, Any]:
        body = super().to_response_body()
        body["error"]["mapping"] = self.report
        return body


class ContentStoreError(PublicationError):
    """The customer's content store failed transiently (safe to retry)."""

    status_code = 503
    code = "storage_unavailable"
    retryable = True
