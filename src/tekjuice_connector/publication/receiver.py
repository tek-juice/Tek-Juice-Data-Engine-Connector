"""Framework-neutral publication receiver.

:class:`PublicationReceiver` turns a raw HTTP request (headers + body bytes)
into a :class:`ReceiverResponse` (status + JSON body). Framework adapters in
``tekjuice_connector.adapters`` only translate their framework's request and
response objects; all contract, authentication, idempotency and duplicate
rules live here so every framework behaves identically.

Semantics
---------
create
    - Same ``idempotency_key`` already applied: replay the stored result
      (``replayed: true``); nothing is written.
    - Slug free: create.
    - Slug held by content from the same ``execution_id`` (regenerated
      content for the same execution): update it in place.
    - Slug held by different content: ``409 duplicate_slug``.
update
    - Requires ``publish.update`` support from the store, else ``422``.
    - Slug not found: ``404 target_not_found``. Updates never create.
verify
    - Authenticates, validates the embedded publish request exactly as a
      real publish would (including duplicate-slug and store validation),
      and reports the URL it would publish to. Never writes.
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Union

from tekjuice_connector.publication.contract import (
    ACTION_CREATE,
    ACTION_UPDATE,
    ACTION_VERIFY,
    CAPABILITY_CREATE,
    CAPABILITY_UPDATE,
    CONTRACT_VERSION,
    PublishRequest,
    SUPPORTED_CONTRACT_VERSIONS,
    check_contract_version,
    parse_publish_request,
    parse_verify_request,
)
from tekjuice_connector.publication.errors import (
    ContentStoreError,
    DuplicateSlugError,
    MalformedRequestError,
    PublicationError,
    ReceiverAuthenticationError,
    RequestTooLargeError,
    TargetNotFoundError,
    UnsupportedOperationError,
    WebsiteMismatchError,
)
from tekjuice_connector.publication.store import StoredContent, store_capabilities
from tekjuice_connector.security.secrets import SecretStr

_log = logging.getLogger(__name__)

MIN_RECEIVER_SECRET_LENGTH = 32
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ReceiverResponse:
    """Framework-neutral HTTP response."""

    status_code: int
    body: Dict[str, Any]
    headers: Dict[str, str] = field(
        default_factory=lambda: {"Content-Type": "application/json"}
    )

    def body_bytes(self) -> bytes:
        return json.dumps(self.body, separators=(",", ":")).encode("utf-8")


class PublicationReceiver:
    """Receives publications from the Tek Juice Data Engine.

    Parameters
    ----------
    store:
        The customer's :class:`~tekjuice_connector.publication.store.ContentStore`.
    website_id:
        The Tek Juice website ID this backend serves. Requests for any other
        website are rejected.
    receiver_secret:
        Shared bearer secret the Data Engine presents. Generate it with
        ``secrets.token_urlsafe(48)``, keep it server-side, and register it
        through the connector handshake. At least 32 characters.
    max_body_bytes:
        Requests larger than this are rejected with 413.
    """

    def __init__(
        self,
        *,
        store: object,
        website_id: str,
        receiver_secret: Union[str, SecretStr],
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        secret = (
            receiver_secret.reveal()
            if isinstance(receiver_secret, SecretStr)
            else str(receiver_secret or "")
        )
        if len(secret) < MIN_RECEIVER_SECRET_LENGTH:
            raise ValueError(
                f"receiver_secret must be at least {MIN_RECEIVER_SECRET_LENGTH} characters"
            )
        if not website_id:
            raise ValueError("website_id is required")
        for method in ("find_by_slug", "find_by_idempotency_key", "create", "public_url"):
            if not callable(getattr(store, method, None)):
                raise TypeError(
                    f"Content store {type(store).__name__} must implement {method}()"
                )

        self._store = store
        self._website_id = str(website_id)
        self._secret = SecretStr(secret)
        self._max_body_bytes = max_body_bytes
        self.capabilities = store_capabilities(store)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def website_id(self) -> str:
        return self._website_id

    @property
    def receiver_secret(self) -> SecretStr:
        return self._secret

    @property
    def max_body_bytes(self) -> int:
        return self._max_body_bytes

    @property
    def contract_versions(self) -> frozenset:
        return SUPPORTED_CONTRACT_VERSIONS

    def handle(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> ReceiverResponse:
        """Process one request. Never raises: errors become JSON responses."""
        try:
            return self._handle(headers, body)
        except PublicationError as exc:
            _log.warning("Publication request rejected: %s (%s)", exc.code, exc.message)
            return ReceiverResponse(exc.status_code, exc.to_response_body())
        except Exception:  # noqa: BLE001
            # Unexpected store failure: report a permanent error without
            # leaking internals; the customer's own logs hold the traceback.
            _log.exception("Unexpected error while receiving a publication")
            error = PublicationError("Receiver failed to process the publication")
            return ReceiverResponse(error.status_code, error.to_response_body())

    # ------------------------------------------------------------------
    # Request processing
    # ------------------------------------------------------------------

    def _handle(self, headers: Mapping[str, str], body: bytes) -> ReceiverResponse:
        self._authenticate(headers)

        if len(body) > self._max_body_bytes:
            raise RequestTooLargeError(
                f"Request body exceeds {self._max_body_bytes} bytes"
            )

        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise MalformedRequestError("Request body is not valid JSON") from exc

        if not isinstance(data, dict):
            raise MalformedRequestError("Request body must be a JSON object")

        check_contract_version(data)

        if str(data.get("website_id") or "") != self._website_id:
            raise WebsiteMismatchError(
                "This receiver does not serve the requested website"
            )

        action = data.get("action")

        if action == ACTION_VERIFY:
            return self._verify(data)

        request = parse_publish_request(data)
        if request.action == ACTION_CREATE:
            return self._create(request)
        return self._update(request)

    def _authenticate(self, headers: Mapping[str, str]) -> None:
        authorization = _header(headers, "authorization") or ""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise ReceiverAuthenticationError("Missing bearer authorization")
        if not hmac.compare_digest(
            token.strip().encode("utf-8"), self._secret.reveal().encode("utf-8")
        ):
            raise ReceiverAuthenticationError("Invalid receiver credential")

    def _require(self, capability: str, action: str) -> None:
        if capability not in self.capabilities:
            raise UnsupportedOperationError(
                f"This receiver's content store does not support '{action}'"
            )

    def _verify(self, data: Dict[str, Any]) -> ReceiverResponse:
        request = parse_verify_request(data)
        publication = request.publication

        if publication.action == ACTION_CREATE:
            self._require(CAPABILITY_CREATE, ACTION_CREATE)
            self._resolve_create_target(publication)
        else:
            self._require(CAPABILITY_UPDATE, ACTION_UPDATE)
            self._resolve_update_target(publication)

        validate = getattr(self._store, "validate", None)
        if callable(validate):
            validate(publication.content)

        return ReceiverResponse(
            200,
            {
                "status": "verified",
                "contract_version": CONTRACT_VERSION,
                "challenge": request.challenge,
                "website_id": self._website_id,
                "persisted": False,
                "capabilities": sorted(self.capabilities),
                "contract_versions": sorted(SUPPORTED_CONTRACT_VERSIONS),
                "would_publish_url": self._store.public_url(publication.content.slug),
            },
        )

    def _resolve_create_target(self, request: PublishRequest) -> Optional[StoredContent]:
        """Return content to overwrite for a create, or None to insert new."""
        existing = self._store.find_by_slug(request.content.slug)
        if existing is None:
            return None
        if existing.execution_id == request.execution_id:
            return existing
        raise DuplicateSlugError(
            f"Content slug already exists: {request.content.slug}"
        )

    def _resolve_update_target(self, request: PublishRequest) -> StoredContent:
        existing = self._store.find_by_slug(request.content.slug)
        if existing is None:
            raise TargetNotFoundError(
                f"Content slug not found: {request.content.slug}"
            )
        return existing

    def _replay(self, request: PublishRequest) -> Optional[ReceiverResponse]:
        stored = self._store.find_by_idempotency_key(request.idempotency_key)
        if stored is None:
            return None
        return self._published(request, stored, performed="none", replayed=True)

    def _create(self, request: PublishRequest) -> ReceiverResponse:
        self._require(CAPABILITY_CREATE, ACTION_CREATE)

        replay = self._replay(request)
        if replay is not None:
            return replay

        existing = self._resolve_create_target(request)

        if existing is not None:
            stored = self._write_update(existing, request)
            return self._published(request, stored, performed="updated")

        try:
            stored = self._store.create(
                request.content,
                execution_id=request.execution_id,
                idempotency_key=request.idempotency_key,
            )
        except DuplicateSlugError:
            # Lost a race for the slug: re-resolve against the winner.
            replay = self._replay(request)
            if replay is not None:
                return replay
            existing = self._resolve_create_target(request)
            if existing is None:
                raise
            stored = self._write_update(existing, request)
            return self._published(request, stored, performed="updated")

        return self._published(request, stored, performed="created")

    def _update(self, request: PublishRequest) -> ReceiverResponse:
        self._require(CAPABILITY_UPDATE, ACTION_UPDATE)

        replay = self._replay(request)
        if replay is not None:
            return replay

        existing = self._resolve_update_target(request)
        stored = self._write_update(existing, request)
        return self._published(request, stored, performed="updated")

    def _write_update(self, existing: StoredContent, request: PublishRequest) -> StoredContent:
        update = getattr(self._store, "update", None)
        if not callable(update):
            raise UnsupportedOperationError(
                "This receiver's content store cannot overwrite existing content"
            )
        return update(
            existing,
            request.content,
            execution_id=request.execution_id,
            idempotency_key=request.idempotency_key,
        )

    def _published(
        self,
        request: PublishRequest,
        stored: StoredContent,
        *,
        performed: str,
        replayed: bool = False,
    ) -> ReceiverResponse:
        if not isinstance(stored, StoredContent):
            raise PublicationError("Content store did not return StoredContent")
        return ReceiverResponse(
            200,
            {
                "status": "published",
                "contract_version": CONTRACT_VERSION,
                "action": request.action,
                "action_performed": performed,
                "replayed": replayed,
                "persisted": True,
                "website_id": self._website_id,
                "execution_id": request.execution_id,
                "idempotency_key": request.idempotency_key,
                "resource_id": stored.resource_id,
                "slug": stored.slug,
                "content_hash": stored.content_hash,
                "published_url": stored.published_url,
            },
        )

    def __repr__(self) -> str:
        return (
            f"PublicationReceiver(website_id={self._website_id!r}, "
            f"capabilities={sorted(self.capabilities)!r})"
        )


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None
