"""Tek Juice publication contract v1.0 — receiver side.

The Data Engine sends two kinds of request to the customer backend's
publication endpoint, both as ``POST`` with a JSON body and the headers::

    Authorization: Bearer <receiver secret>
    X-TekJuice-Contract-Version: 1.0
    Content-Type: application/json

Publish (``action`` = ``create`` | ``update``)::

    {
      "contract_version": "1.0",
      "execution_id": "<Data Engine execution id>",
      "website_id": "<website id>",
      "action": "create",
      "idempotency_key": "<deterministic key>",
      "content": {
        "title": "...", "body": "<html>", "slug": "lowercase-hyphen-slug",
        "excerpt": "..." | null, "meta_description": "..." | null,
        "meta_keywords": ["..."]
      },
      "metadata": {
        "content_strategy_id": "..." | null,
        "opportunity_id": "..." | null,
        "content_hash": "<sha256 hex>"
      }
    }

Verify (non-persisting publication readiness check)::

    {
      "contract_version": "1.0",
      "action": "verify",
      "website_id": "<website id>",
      "challenge": "<one-time value to echo>",
      "dry_run": true,
      "publication": { <a complete publish request, validated but not stored> }
    }

Responses are documented in ``docs/publication-contract.md``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tekjuice_connector.publication.errors import (
    ContractViolationError,
    UnsupportedContractVersionError,
)


CONTRACT_VERSION = "1.0"
SUPPORTED_CONTRACT_VERSIONS = frozenset({CONTRACT_VERSION})

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_VERIFY = "verify"

CAPABILITY_CREATE = "publish.create"
CAPABILITY_UPDATE = "publish.update"
CAPABILITY_VERIFY = "publish.verify"

SLUG_MAX_LENGTH = 120
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_PUBLISH_FIELDS = frozenset(
    {
        "contract_version",
        "execution_id",
        "website_id",
        "action",
        "idempotency_key",
        "content",
        "metadata",
    }
)
_CONTENT_FIELDS = frozenset(
    {"title", "body", "slug", "excerpt", "meta_description", "meta_keywords"}
)
_METADATA_FIELDS = frozenset(
    {"content_strategy_id", "opportunity_id", "content_hash"}
)
_VERIFY_FIELDS = frozenset(
    {"contract_version", "action", "website_id", "challenge", "dry_run", "publication"}
)


@dataclass(frozen=True)
class PublicationContent:
    """Content to persist. ``body`` is HTML produced by the Data Engine."""

    title: str
    body: str
    slug: str
    excerpt: Optional[str] = None
    meta_description: Optional[str] = None
    meta_keywords: List[str] = field(default_factory=list)

    def content_hash(self) -> str:
        """SHA-256 over the canonical content string (matches the Data Engine)."""
        canonical = "\n".join(
            [
                self.title,
                self.body,
                self.slug,
                self.excerpt or "",
                self.meta_description or "",
                ",".join(self.meta_keywords),
            ]
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PublishRequest:
    """A validated create/update request."""

    contract_version: str
    execution_id: str
    website_id: str
    action: str
    idempotency_key: str
    content: PublicationContent
    content_strategy_id: Optional[str] = None
    opportunity_id: Optional[str] = None


@dataclass(frozen=True)
class VerifyRequest:
    """A validated readiness verification request."""

    contract_version: str
    website_id: str
    challenge: str
    publication: PublishRequest


def is_valid_slug(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= SLUG_MAX_LENGTH
        and _SLUG_PATTERN.fullmatch(value) is not None
    )


def _require_object(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractViolationError(f"'{name}' must be a JSON object")
    return value


def _reject_unknown(data: Dict[str, Any], allowed: frozenset, name: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ContractViolationError(
            f"Unknown field(s) in {name}: {', '.join(unknown)}"
        )


def _require_str(data: Dict[str, Any], key: str, name: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractViolationError(f"'{name}.{key}' must be a non-empty string")
    return value


def _optional_str(data: Dict[str, Any], key: str, name: str) -> Optional[str]:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractViolationError(f"'{name}.{key}' must be a string or null")
    return value


def check_contract_version(data: Dict[str, Any]) -> str:
    version = data.get("contract_version")
    if version not in SUPPORTED_CONTRACT_VERSIONS:
        raise UnsupportedContractVersionError(
            f"Unsupported contract_version {version!r}; this receiver supports "
            f"{sorted(SUPPORTED_CONTRACT_VERSIONS)}"
        )
    return str(version)


def parse_publish_request(data: Any) -> PublishRequest:
    """Validate a create/update request. Raises ContractViolationError."""
    data = _require_object(data, "request")
    version = check_contract_version(data)
    _reject_unknown(data, _PUBLISH_FIELDS, "request")

    action = data.get("action")
    if action not in (ACTION_CREATE, ACTION_UPDATE):
        raise ContractViolationError(
            f"'request.action' must be 'create' or 'update', got {action!r}"
        )

    content_data = _require_object(data.get("content"), "content")
    _reject_unknown(content_data, _CONTENT_FIELDS, "content")

    slug = _require_str(content_data, "slug", "content")
    if not is_valid_slug(slug):
        raise ContractViolationError(
            f"'content.slug' {slug!r} must be lowercase letters, digits and "
            f"single hyphens, at most {SLUG_MAX_LENGTH} characters"
        )

    keywords = content_data.get("meta_keywords", [])
    if not isinstance(keywords, list) or not all(
        isinstance(k, str) for k in keywords
    ):
        raise ContractViolationError("'content.meta_keywords' must be a list of strings")

    content = PublicationContent(
        title=_require_str(content_data, "title", "content"),
        body=_require_str(content_data, "body", "content"),
        slug=slug,
        excerpt=_optional_str(content_data, "excerpt", "content"),
        meta_description=_optional_str(content_data, "meta_description", "content"),
        meta_keywords=list(keywords),
    )

    metadata = data.get("metadata") or {}
    metadata = _require_object(metadata, "metadata")
    _reject_unknown(metadata, _METADATA_FIELDS, "metadata")

    declared_hash = _optional_str(metadata, "content_hash", "metadata")
    if declared_hash is not None:
        if not _SHA256_PATTERN.fullmatch(declared_hash):
            raise ContractViolationError("'metadata.content_hash' must be a sha256 hex digest")
        if declared_hash != content.content_hash():
            raise ContractViolationError(
                "'metadata.content_hash' does not match the content; the "
                "payload was altered or serialised incorrectly"
            )

    return PublishRequest(
        contract_version=version,
        execution_id=_require_str(data, "execution_id", "request"),
        website_id=_require_str(data, "website_id", "request"),
        action=action,
        idempotency_key=_require_str(data, "idempotency_key", "request"),
        content=content,
        content_strategy_id=_optional_str(metadata, "content_strategy_id", "metadata"),
        opportunity_id=_optional_str(metadata, "opportunity_id", "metadata"),
    )


def parse_verify_request(data: Any) -> VerifyRequest:
    """Validate a verify request, including its embedded publish request."""
    data = _require_object(data, "request")
    version = check_contract_version(data)
    _reject_unknown(data, _VERIFY_FIELDS, "request")

    if data.get("dry_run") is not True:
        raise ContractViolationError("'request.dry_run' must be true for verify")

    publication = parse_publish_request(data.get("publication"))
    website_id = _require_str(data, "website_id", "request")

    if publication.website_id != website_id:
        raise ContractViolationError(
            "'publication.website_id' must match 'request.website_id'"
        )

    return VerifyRequest(
        contract_version=version,
        website_id=website_id,
        challenge=_require_str(data, "challenge", "request"),
        publication=publication,
    )
