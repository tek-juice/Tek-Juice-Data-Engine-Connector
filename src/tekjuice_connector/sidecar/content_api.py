"""Content store over the customer's content API (any language).

The sidecar receives S2S publications and hands the validated content to the
customer's backend through a small HTTP API the backend implements in its
own language and framework (docs/sidecar.md, "Content API"):

====================================  =========================================
``GET  {base}/content/{slug}``          200 ``{"slug", "url", "id"}`` or 404
``POST {base}/content``                 201/200 ``{"slug", "url", "id"}``; 409 slug
                                        taken by other content; 422 invalid
``PUT  {base}/content/{slug}``          200 ``{"slug", "url", "id"}``; 404; 422
``POST {base}/content/validate``        optional: 200 or 422 ``{"errors": [...]}``
``GET  {base}/capabilities``            optional: ``{"update": true,
                                        "validate": false,
                                        "public_url_template": "https://.../{slug}"}``
====================================  =========================================

Requests carry ``Authorization: Bearer <TEKJUICE_CONTENT_API_TOKEN>`` (when
configured), ``Idempotency-Key`` and JSON bodies with the content fields plus
``execution_id``, ``idempotency_key`` and ``content_hash``.

The sidecar keeps the idempotency ledger itself (SQLite on its data volume):
which slugs Tek Juice published, by which execution and idempotency key. The
backend therefore needs no idempotency logic; it should store
``idempotency_key`` with the content so a create interrupted after the
backend wrote it is recognised (GET returns it) instead of conflicting.
"""

from __future__ import annotations

import ipaddress
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote, urlsplit

from tekjuice_connector.publication.contract import PublicationContent
from tekjuice_connector.publication.errors import (
    ContentStoreError,
    ContractViolationError,
    DuplicateSlugError,
    PublicationError,
    TargetNotFoundError,
)
from tekjuice_connector.publication.store import StoredContent

# (method, url, headers, json body or None, timeout) -> (status, json body or None)
Request = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]], float], Tuple[int, Any]]


class ContentApiConfigurationError(PublicationError):
    """The sidecar cannot use the content API as configured (permanent)."""

    status_code = 500
    code = "content_api_misconfigured"


def _requests_call(method: str, url: str, headers: Dict[str, str], body: Optional[Dict[str, Any]],
                   timeout: float) -> Tuple[int, Any]:
    import requests

    response = requests.request(method, url, headers=headers, json=body, timeout=(5.0, timeout),
                                allow_redirects=False)
    try:
        data = response.json()
    except ValueError:
        data = None
    return response.status_code, data


def is_private_url(url: str) -> bool:
    """True for loopback, private-network IPs and single-label hosts
    (container service names such as ``http://backend:3000``)."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or "." not in host or host.endswith(".internal") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


class HttpContentStore:
    """``ContentStore`` delegating to the customer's content API."""

    def __init__(
        self,
        base_url: str,
        *,
        ledger_path: str,
        token: Optional[str] = None,
        public_url_template: Optional[str] = None,
        timeout: float = 20.0,
        request: Optional[Request] = None,
    ) -> None:
        parts = urlsplit(base_url or "")
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("TEKJUICE_CONTENT_API_URL must be an absolute http(s) URL")
        if not token and not is_private_url(base_url):
            raise ValueError(
                "TEKJUICE_CONTENT_API_TOKEN is required for a content API outside a private network")
        if parts.scheme == "http" and not is_private_url(base_url):
            raise ValueError("A content API on a public host must use https")
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._request = request or _requests_call
        self._ledger_path = ledger_path
        self._lock = threading.Lock()
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tekjuice_ledger (
                slug TEXT PRIMARY KEY, resource_id TEXT NOT NULL, execution_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, content_hash TEXT NOT NULL, published_url TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS tekjuice_ledger_key ON tekjuice_ledger (idempotency_key)")
            # A copy of what was published (public content) so the sidecar can
            # serve discoverability outputs, and the approved site overrides.
            db.execute("""CREATE TABLE IF NOT EXISTS tekjuice_content_copy (
                slug TEXT PRIMARY KEY, content TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS tekjuice_site_overrides (
                path TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        capabilities = self._capabilities()
        self._template = public_url_template or capabilities.get("public_url_template")
        if not self._template or "{slug}" not in self._template:
            raise ValueError(
                "The public URL of published content is unknown: set TEKJUICE_PUBLIC_URL_TEMPLATE "
                "(e.g. https://www.example.com/blog/{slug}) or return public_url_template from "
                "GET {content API}/capabilities")
        if capabilities.get("update") is False:
            self.update = None  # type: ignore[assignment]  # no publish.update capability
        self._remote_validate = bool(capabilities.get("validate"))

    # ------------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._ledger_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
              idempotency_key: Optional[str] = None) -> Tuple[int, Any]:
        try:
            status, data = self._request(method, self._base + path, self._headers(idempotency_key), body,
                                         self._timeout)
        except Exception as exc:  # noqa: BLE001 - every transport failure is transient
            raise ContentStoreError(f"Content API unreachable ({type(exc).__name__})") from exc
        if status in (401, 403):
            raise ContentApiConfigurationError(
                f"The content API rejected the sidecar's credential (HTTP {status}): check "
                "TEKJUICE_CONTENT_API_TOKEN on both sides")
        if status >= 500 or status == 429:
            raise ContentStoreError(f"Content API failed (HTTP {status})")
        return status, data

    def _capabilities(self) -> Dict[str, Any]:
        try:
            status, data = self._call("GET", "/capabilities")
        except ContentStoreError:
            return {}
        return data if status == 200 and isinstance(data, dict) else {}

    @staticmethod
    def _payload(content: PublicationContent, execution_id: str, idempotency_key: str) -> Dict[str, Any]:
        return {
            "slug": content.slug, "title": content.title, "body": content.body, "excerpt": content.excerpt,
            "meta_description": content.meta_description, "meta_keywords": list(content.meta_keywords),
            "discovery": content.discovery, "execution_id": execution_id, "idempotency_key": idempotency_key,
            "content_hash": content.content_hash(),
        }

    def _record(self, slug: str, data: Any, execution_id: str, idempotency_key: str, content_hash: str,
                resource_id: Optional[str] = None, content: Optional[PublicationContent] = None) -> StoredContent:
        data = data if isinstance(data, dict) else {}
        stored = StoredContent(
            resource_id=str(data.get("id") or resource_id or uuid.uuid4()),
            slug=slug,
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            content_hash=content_hash,
            published_url=str(data.get("url") or self.public_url(slug)),
        )
        with self._lock, self._db() as db:
            db.execute("""INSERT INTO tekjuice_ledger VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET resource_id=excluded.resource_id, execution_id=excluded.execution_id,
                idempotency_key=excluded.idempotency_key, content_hash=excluded.content_hash,
                published_url=excluded.published_url, updated_at=excluded.updated_at""",
                       (stored.slug, stored.resource_id, stored.execution_id, stored.idempotency_key,
                        stored.content_hash, stored.published_url, datetime.now(timezone.utc).isoformat()))
            if content is not None:
                db.execute("""INSERT INTO tekjuice_content_copy VALUES (?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at""",
                           (slug, json.dumps(_content_dict(content)), datetime.now(timezone.utc).isoformat()))
        return stored

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[StoredContent]:
        if row is None:
            return None
        return StoredContent(resource_id=row["resource_id"], slug=row["slug"], execution_id=row["execution_id"],
                             idempotency_key=row["idempotency_key"], content_hash=row["content_hash"],
                             published_url=row["published_url"])

    # ContentStore -------------------------------------------------------

    def public_url(self, slug: str) -> str:
        return self._template.replace("{slug}", slug)

    def find_by_slug(self, slug: str) -> Optional[StoredContent]:
        with self._db() as db:
            return self._row(db.execute("SELECT * FROM tekjuice_ledger WHERE slug = ?", (slug,)).fetchone())

    def find_by_idempotency_key(self, idempotency_key: str) -> Optional[StoredContent]:
        with self._db() as db:
            return self._row(db.execute("SELECT * FROM tekjuice_ledger WHERE idempotency_key = ?",
                                        (idempotency_key,)).fetchone())

    def create(self, content: PublicationContent, *, execution_id: str, idempotency_key: str) -> StoredContent:
        status, data = self._call("POST", "/content", self._payload(content, execution_id, idempotency_key),
                                  idempotency_key)
        if status in (200, 201):
            return self._record(content.slug, data, execution_id, idempotency_key, content.content_hash(),
                                content=content)
        if status == 409:
            # Our own earlier create, interrupted before the ledger write?
            existing_status, existing = self._call("GET", f"/content/{quote(content.slug, safe='')}")
            if (existing_status == 200 and isinstance(existing, dict)
                    and existing.get("idempotency_key") == idempotency_key):
                return self._record(content.slug, existing, execution_id, idempotency_key,
                                    content.content_hash(), content=content)
            raise DuplicateSlugError(f"The backend already has other content at slug {content.slug!r}")
        raise self._rejection(status, data)

    def update(self, existing: StoredContent, content: PublicationContent, *, execution_id: str,
               idempotency_key: str) -> StoredContent:
        status, data = self._call("PUT", f"/content/{quote(existing.slug, safe='')}",
                                  self._payload(content, execution_id, idempotency_key), idempotency_key)
        if status == 200:
            return self._record(existing.slug, data, execution_id, idempotency_key, content.content_hash(),
                                existing.resource_id, content=content)
        if status == 404:
            raise TargetNotFoundError(f"The backend no longer has content at slug {existing.slug!r}")
        raise self._rejection(status, data)

    def validate(self, content: PublicationContent) -> None:
        """Dry-run check: the backend accepts the content (``/content/validate``)
        or, without that endpoint, is reachable and accepts the credential."""
        if self._remote_validate:
            status, data = self._call("POST", "/content/validate", self._payload(content, "verify", "verify"))
            if status != 200:
                raise self._rejection(status, data)
            return
        status, _ = self._call("GET", f"/content/{quote(content.slug, safe='')}")
        if status not in (200, 404):
            raise ContentStoreError(f"Content API answered HTTP {status} to a read")

    # Discoverability (SeoPages) and site changes ------------------------

    def get_content(self, slug: str) -> Optional[PublicationContent]:
        with self._db() as db:
            row = db.execute("SELECT content FROM tekjuice_content_copy WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["content"])
        return PublicationContent(title=data["title"], body=data["body"], slug=data["slug"],
                                  excerpt=data.get("excerpt"), meta_description=data.get("meta_description"),
                                  meta_keywords=list(data.get("meta_keywords") or []),
                                  discovery=data.get("discovery"))

    def list_published(self):
        from tekjuice_connector.publication.seo import PublishedPage

        with self._db() as db:
            rows = db.execute("SELECT slug, updated_at FROM tekjuice_content_copy ORDER BY slug").fetchall()
        return [PublishedPage(slug=r["slug"], updated_at=datetime.fromisoformat(r["updated_at"])) for r in rows]

    def get_site_override(self, path: str) -> Optional[Dict]:
        with self._db() as db:
            row = db.execute("SELECT data FROM tekjuice_site_overrides WHERE path = ?", (path,)).fetchone()
        return json.loads(row["data"]) if row else None

    def set_site_override(self, path: str, override: Dict) -> None:
        with self._lock, self._db() as db:
            db.execute("""INSERT INTO tekjuice_site_overrides VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
                       (path, json.dumps(override), datetime.now(timezone.utc).isoformat()))

    def delete_site_override(self, path: str) -> None:
        with self._lock, self._db() as db:
            db.execute("DELETE FROM tekjuice_site_overrides WHERE path = ?", (path,))

    def list_site_overrides(self) -> Dict[str, Dict]:
        with self._db() as db:
            return {r["path"]: json.loads(r["data"]) for r in
                    db.execute("SELECT path, data FROM tekjuice_site_overrides").fetchall()}

    @staticmethod
    def _rejection(status: int, data: Any) -> PublicationError:
        errors = data.get("errors") if isinstance(data, dict) else None
        detail = "; ".join(str(e) for e in errors[:5]) if isinstance(errors, list) else f"HTTP {status}"
        return ContractViolationError(f"The backend rejected the content: {detail}")

    def __repr__(self) -> str:
        return f"HttpContentStore(base_url={self._base!r}, authenticated={bool(self._token)})"


def _content_dict(content: PublicationContent) -> Dict[str, Any]:
    return {"title": content.title, "body": content.body, "slug": content.slug, "excerpt": content.excerpt,
            "meta_description": content.meta_description, "meta_keywords": list(content.meta_keywords),
            "discovery": content.discovery}


def describe_content_api(store: HttpContentStore) -> str:
    return json.dumps({"content_api": store._base, "authenticated": bool(store._token)})  # noqa: SLF001
