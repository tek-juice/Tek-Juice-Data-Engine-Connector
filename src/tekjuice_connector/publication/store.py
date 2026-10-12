"""Content store interface — the customer's persistence model.

The receiver never assumes a framework, ORM, table or route. The customer
implements :class:`ContentStore` over whatever persists their pages (an ORM
model, a CMS API, files, a headless CMS...). Optional methods determine the
capabilities the receiver advertises to the Data Engine:

==================  =====================  ================================
Method              Required               Capability
==================  =====================  ================================
``find_by_slug``    yes
``find_by_idempotency_key``  yes
``create``          yes                    ``publish.create``
``public_url``      yes
``update``          optional               ``publish.update``
``validate``        optional               (extra dry-run checks)
==================  =====================  ================================

Contract for implementers
-------------------------
- ``create``/``update`` must persist ``idempotency_key`` and ``execution_id``
  together with the content, atomically, so a redelivered request is
  recognised by ``find_by_idempotency_key``.
- Slugs must be unique. If a concurrent writer wins a race for a slug,
  raise :class:`~tekjuice_connector.publication.errors.DuplicateSlugError`.
- Raise :class:`~tekjuice_connector.publication.errors.ContentStoreError`
  for transient failures (database unavailable, lock timeout); the Data
  Engine will retry. Any other exception is reported as a permanent error.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Dict, Optional, Protocol, runtime_checkable

from tekjuice_connector.publication.contract import (
    CAPABILITY_CREATE,
    CAPABILITY_UPDATE,
    CAPABILITY_VERIFY,
    PublicationContent,
)
from tekjuice_connector.publication.errors import (
    ContentStoreError,
    DuplicateSlugError,
)


@dataclass(frozen=True)
class StoredContent:
    """What the receiver needs to know about persisted content."""

    resource_id: str
    slug: str
    execution_id: str
    idempotency_key: str
    content_hash: str
    published_url: str


@runtime_checkable
class ContentStore(Protocol):
    """Minimum persistence interface for receiving publications."""

    def find_by_slug(self, slug: str) -> Optional[StoredContent]:
        """Return Tek Juice-managed content with this slug, or ``None``."""
        ...

    def find_by_idempotency_key(self, idempotency_key: str) -> Optional[StoredContent]:
        """Return content last written by this idempotency key, or ``None``."""
        ...

    def create(
        self,
        content: PublicationContent,
        *,
        execution_id: str,
        idempotency_key: str,
    ) -> StoredContent:
        ...

    def public_url(self, slug: str) -> str:
        """Return the canonical public URL content with this slug is served at."""
        ...


def store_capabilities(store: object) -> frozenset:
    """Capabilities a receiver backed by *store* can honestly advertise."""
    capabilities = {CAPABILITY_VERIFY}
    if callable(getattr(store, "create", None)):
        capabilities.add(CAPABILITY_CREATE)
    if callable(getattr(store, "update", None)):
        capabilities.add(CAPABILITY_UPDATE)
    return frozenset(capabilities)


# ---------------------------------------------------------------------------
# Reference implementations
# ---------------------------------------------------------------------------


class InMemoryContentStore:
    """Thread-safe in-memory store for tests and local development."""

    def __init__(self, public_base_url: str, *, supports_update: bool = True) -> None:
        self._base = public_base_url.rstrip("/")
        self._by_slug: Dict[str, StoredContent] = {}
        self._content: Dict[str, PublicationContent] = {}
        self._lock = threading.Lock()
        if not supports_update:
            # Instance attribute shadows the method so capability detection
            # sees an update-less store.
            self.update = None  # type: ignore[assignment]

    def public_url(self, slug: str) -> str:
        return f"{self._base}/{slug}"

    def find_by_slug(self, slug: str) -> Optional[StoredContent]:
        return self._by_slug.get(slug)

    def find_by_idempotency_key(self, idempotency_key: str) -> Optional[StoredContent]:
        for stored in self._by_slug.values():
            if stored.idempotency_key == idempotency_key:
                return stored
        return None

    def get_content(self, slug: str) -> Optional[PublicationContent]:
        return self._content.get(slug)

    def create(self, content, *, execution_id, idempotency_key) -> StoredContent:
        with self._lock:
            if content.slug in self._by_slug:
                raise DuplicateSlugError(f"Content slug already exists: {content.slug}")
            stored = StoredContent(
                resource_id=str(uuid.uuid4()),
                slug=content.slug,
                execution_id=execution_id,
                idempotency_key=idempotency_key,
                content_hash=content.content_hash(),
                published_url=self.public_url(content.slug),
            )
            self._by_slug[content.slug] = stored
            self._content[content.slug] = content
            return stored

    def update(self, existing, content, *, execution_id, idempotency_key) -> StoredContent:
        with self._lock:
            stored = replace(
                existing,
                execution_id=execution_id,
                idempotency_key=idempotency_key,
                content_hash=content.content_hash(),
            )
            self._by_slug[existing.slug] = stored
            self._content[existing.slug] = content
            return stored


class SQLiteContentStore:
    """Persistent reference store on the standard-library ``sqlite3``.

    Useful for small backends without an ORM and as a worked example of the
    atomicity rules above. One connection per call keeps it thread-safe.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS tekjuice_content (
            resource_id      TEXT PRIMARY KEY,
            slug             TEXT NOT NULL UNIQUE,
            title            TEXT NOT NULL,
            body             TEXT NOT NULL,
            excerpt          TEXT,
            meta_description TEXT,
            meta_keywords    TEXT NOT NULL DEFAULT '',
            execution_id     TEXT NOT NULL,
            idempotency_key  TEXT NOT NULL UNIQUE,
            content_hash     TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
    """

    def __init__(self, path: str, public_base_url: str) -> None:
        self._path = path
        self._base = public_base_url.rstrip("/")
        with self._connect() as conn:
            conn.execute(self._SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self._path, timeout=5.0)
        except sqlite3.Error as exc:
            raise ContentStoreError(f"Content store unavailable: {exc}") from exc
        conn.row_factory = sqlite3.Row
        return conn

    def public_url(self, slug: str) -> str:
        return f"{self._base}/{slug}"

    def _stored(self, row: Optional[sqlite3.Row]) -> Optional[StoredContent]:
        if row is None:
            return None
        return StoredContent(
            resource_id=row["resource_id"],
            slug=row["slug"],
            execution_id=row["execution_id"],
            idempotency_key=row["idempotency_key"],
            content_hash=row["content_hash"],
            published_url=self.public_url(row["slug"]),
        )

    def _fetch(self, column: str, value: str) -> Optional[StoredContent]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM tekjuice_content WHERE {column} = ?", (value,)
            ).fetchone()
        return self._stored(row)

    def find_by_slug(self, slug: str) -> Optional[StoredContent]:
        return self._fetch("slug", slug)

    def find_by_idempotency_key(self, idempotency_key: str) -> Optional[StoredContent]:
        return self._fetch("idempotency_key", idempotency_key)

    def get_row(self, slug: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tekjuice_content WHERE slug = ?", (slug,)
            ).fetchone()
        return dict(row) if row is not None else None

    def create(self, content, *, execution_id, idempotency_key) -> StoredContent:
        resource_id = str(uuid.uuid4())
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tekjuice_content
                        (resource_id, slug, title, body, excerpt, meta_description,
                         meta_keywords, execution_id, idempotency_key,
                         content_hash, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resource_id,
                        content.slug,
                        content.title,
                        content.body,
                        content.excerpt,
                        content.meta_description,
                        ",".join(content.meta_keywords),
                        execution_id,
                        idempotency_key,
                        content.content_hash(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateSlugError(
                f"Content slug already exists: {content.slug}"
            ) from exc
        except sqlite3.OperationalError as exc:
            raise ContentStoreError(f"Content store unavailable: {exc}") from exc
        return self.find_by_slug(content.slug)  # type: ignore[return-value]

    def update(self, existing, content, *, execution_id, idempotency_key) -> StoredContent:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE tekjuice_content
                    SET title = ?, body = ?, excerpt = ?, meta_description = ?,
                        meta_keywords = ?, execution_id = ?, idempotency_key = ?,
                        content_hash = ?, updated_at = ?
                    WHERE resource_id = ?
                    """,
                    (
                        content.title,
                        content.body,
                        content.excerpt,
                        content.meta_description,
                        ",".join(content.meta_keywords),
                        execution_id,
                        idempotency_key,
                        content.content_hash(),
                        datetime.now(timezone.utc).isoformat(),
                        existing.resource_id,
                    ),
                )
        except sqlite3.OperationalError as exc:
            raise ContentStoreError(f"Content store unavailable: {exc}") from exc
        return self.find_by_slug(existing.slug)  # type: ignore[return-value]
