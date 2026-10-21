"""Where content types come from, and how records of them are written.

Discovery sources (each returns ``[(ContentTypeCapability, store)]``):

- :func:`native_source`: the backend's own ContentStore (the classic article
  store, any Python framework) as one content type.
- :func:`content_api_source`: the sidecar content API's manifest,
  ``GET {base}/capabilities`` -> ``content_types`` (any language; the
  backend owner lists the types and so enables them).
- :func:`openapi_source`: an OpenAPI 3 document the backend publishes
  (Laravel with Scribe/L5-Swagger, Django REST framework, FastAPI, NestJS,
  Express with swagger-jsdoc, ...). Resources become types; a type is enabled
  by an ``x-tekjuice-content-type`` extension on its create operation or by
  the ``TEKJUICE_CONTENT_TYPES`` allow-list.
- :func:`wordpress_source`: WordPress REST (``/wp-json/wp/v2/types`` and each
  type's schema), with an application password; enabled by the allow-list.

The framework is never taken as proof: only what the backend's own
machine-readable description says is used, and records are written only
through the operations it declares.

:class:`RestResourceStore` writes records of one type through those
operations: payloads mapped and validated by schema_mapping, endpoints
relative to the configured base (never another host), no redirects followed,
credentials only in headers (never logged), and its own idempotency ledger
per content type.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urlencode, urlsplit

from tekjuice_connector.publication.content_types import (
    ContentTypeCapability,
    Endpoint,
    FieldSpec,
    classify,
    normalise_name,
)
from tekjuice_connector.publication.errors import (
    ContentMappingError,
    ContentStoreError,
    ContractViolationError,
    DuplicateSlugError,
    PublicationError,
    TargetNotFoundError,
)
from tekjuice_connector.publication.schema_mapping import fields_from_json_schema, map_publication
from tekjuice_connector.publication.store import StoredContent

# (method, url, headers, json body or None, timeout) -> (status, json body or None)
Request = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]], float], Tuple[int, Any]]
_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/{}-]*$")
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
WORDPRESS_INTERNAL_TYPES = frozenset({"attachment", "nav_menu_item", "wp_block", "wp_template", "wp_template_part",
                                      "wp_navigation", "wp_global_styles", "wp_font_family", "wp_font_face"})
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
RECORD_TEXT_MAX = 50_000


class ContentSourceError(PublicationError):
    """A discovery source is misconfigured (permanent)."""

    status_code = 500
    code = "content_source_misconfigured"


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


def safe_path(path: str) -> str:
    """A path relative to the configured base: never a URL, never '..'."""
    if not isinstance(path, str) or "://" in path or path.startswith("//") or ".." in path \
            or not _PATH.match(path):
        raise ContentSourceError(f"Endpoint path {path!r} must be a path on the configured backend (e.g. /posts)")
    return path


def check_base_url(base_url: str, *, authenticated: bool) -> str:
    from tekjuice_connector.sidecar.content_api import is_private_url

    parts = urlsplit(base_url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ContentSourceError("The backend base URL must be an absolute http(s) URL")
    if parts.scheme == "http" and not is_private_url(base_url):
        raise ContentSourceError("A backend on a public host must use https")
    if not authenticated and not is_private_url(base_url):
        raise ContentSourceError("A backend outside a private network needs a credential")
    return base_url.rstrip("/")


# ── Writing records of one content type ──────────────────────────────────────

def _unwrap(data: Any) -> Dict[str, Any]:
    """The record in a response ({...}, {"data": {...}}, {"item": {...}})."""
    if isinstance(data, dict):
        for key in ("data", "item", "record", "result"):
            if isinstance(data.get(key), dict):
                return data[key]
        return data
    return {}


def _rendered(value: Any) -> Any:
    return value.get("rendered") or value.get("raw") if isinstance(value, dict) else value


class RestResourceStore:
    """ContentStore over one content type's declared operations."""

    def __init__(self, capability: ContentTypeCapability, *, base_url: str, ledger_path: str,
                 auth_header: Optional[str] = None, timeout: float = 20.0,
                 request: Optional[Request] = None) -> None:
        self.capability = capability
        self._base = check_base_url(base_url, authenticated=bool(auth_header))
        self._auth = auth_header
        self._timeout = timeout
        self._request = request or _requests_call
        self._ledger_path = ledger_path
        self._lock = threading.Lock()
        for endpoint in capability.operations.values():
            if endpoint is not None:
                safe_path(endpoint.path)
        if "update" not in capability.operations:
            self.update = None  # type: ignore[assignment]
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tekjuice_typed_ledger (
                content_type TEXT NOT NULL, slug TEXT NOT NULL, resource_id TEXT NOT NULL,
                execution_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, content_hash TEXT NOT NULL,
                published_url TEXT NOT NULL, state TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY (content_type, slug))""")
            db.execute("""CREATE INDEX IF NOT EXISTS tekjuice_typed_ledger_key
                ON tekjuice_typed_ledger (content_type, idempotency_key)""")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._ledger_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _headers(self, idempotency_key: Optional[str]) -> Dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._auth:
            headers["Authorization"] = self._auth
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _url(self, endpoint: Endpoint, values: Dict[str, str]) -> str:
        def fill(match: "re.Match[str]") -> str:
            key = match.group(1)
            if key not in values:
                raise ContentSourceError(f"No value for '{{{key}}}' in {endpoint.path}")
            return quote(str(values[key]), safe="")
        return self._base + _PLACEHOLDER.sub(fill, endpoint.path)

    def _call(self, operation: str, values: Dict[str, str], body: Optional[Dict[str, Any]] = None,
              idempotency_key: Optional[str] = None, query: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
        endpoint = self.capability.operations.get(operation)
        if endpoint is None:
            raise ContentSourceError(f"'{self.capability.name}' has no {operation} operation")
        url = self._url(endpoint, values)
        if query:
            url += ("&" if "?" in url else "?") + urlencode(query)
        try:
            status, data = self._request(endpoint.method, url, self._headers(idempotency_key), body, self._timeout)
        except PublicationError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport failures are transient
            raise ContentStoreError(f"Backend unreachable ({type(exc).__name__})") from exc
        if status in (401, 403):
            raise ContentSourceError(f"The backend refused the credential for {operation} "
                                     f"'{self.capability.name}' (HTTP {status})")
        if 300 <= status < 400:
            raise ContentStoreError(f"The backend redirected the {operation} request (HTTP {status}); "
                                    "redirects are not followed")
        if status >= 500 or status == 429:
            raise ContentStoreError(f"Backend failed (HTTP {status})")
        return status, data

    def _map(self, content: Any) -> Dict[str, Any]:
        mapping = map_publication(self.capability, content)
        if not mapping.ok:
            problems = mapping.missing_required and [f"missing required: {', '.join(mapping.missing_required)}"]
            detail = "; ".join([*(problems or []), *mapping.ambiguous, *mapping.errors][:6])
            raise ContentMappingError(f"The content does not fit '{self.capability.name}': {detail}",
                                      report=mapping.report())
        return mapping.payload

    def _stored(self, slug: str, data: Any, execution_id: str, idempotency_key: str, content_hash: str,
                resource_id: Optional[str] = None) -> StoredContent:
        record = _unwrap(data)
        rid = record.get("id") or record.get("uuid") or record.get("_id") or resource_id
        if rid is None:
            raise ContractViolationError(f"The backend saved '{self.capability.name}' but returned no id")
        url = _rendered(record.get("url") or record.get("link") or record.get("permalink")
                        or record.get("public_url")) or ""
        if not self.capability.public:
            url = ""   # no public page: never reported as one
        elif not url and self.capability.public_url_template:
            url = self.capability.public_url_template.replace("{slug}", slug).replace("{id}", str(rid))
        state = record.get("status") or record.get("state")
        state = str(state) if isinstance(state, (str, int)) else None
        stored = StoredContent(resource_id=str(rid), slug=slug, execution_id=execution_id,
                               idempotency_key=idempotency_key, content_hash=content_hash,
                               published_url=str(url), state=state)
        with self._lock, self._db() as db:
            db.execute("""INSERT INTO tekjuice_typed_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_type, slug) DO UPDATE SET resource_id=excluded.resource_id,
                execution_id=excluded.execution_id, idempotency_key=excluded.idempotency_key,
                content_hash=excluded.content_hash, published_url=excluded.published_url,
                state=excluded.state, updated_at=excluded.updated_at""",
                       (self.capability.name, slug, stored.resource_id, execution_id, idempotency_key,
                        content_hash, stored.published_url, state, datetime.now(timezone.utc).isoformat()))
        return stored

    def _row(self, row: Optional[sqlite3.Row]) -> Optional[StoredContent]:
        if row is None:
            return None
        return StoredContent(resource_id=row["resource_id"], slug=row["slug"], execution_id=row["execution_id"],
                             idempotency_key=row["idempotency_key"], content_hash=row["content_hash"],
                             published_url=row["published_url"], state=row["state"])

    # ContentStore -------------------------------------------------------

    def public_url(self, slug: str) -> str:
        template = self.capability.public_url_template
        return template.replace("{slug}", slug) if self.capability.public and template and "{slug}" in template \
            else ""

    def find_by_slug(self, slug: str) -> Optional[StoredContent]:
        with self._db() as db:
            return self._row(db.execute("SELECT * FROM tekjuice_typed_ledger WHERE content_type = ? AND slug = ?",
                                        (self.capability.name, slug)).fetchone())

    def find_by_idempotency_key(self, idempotency_key: str) -> Optional[StoredContent]:
        with self._db() as db:
            return self._row(db.execute(
                "SELECT * FROM tekjuice_typed_ledger WHERE content_type = ? AND idempotency_key = ?",
                (self.capability.name, idempotency_key)).fetchone())

    def create(self, content: Any, *, execution_id: str, idempotency_key: str) -> StoredContent:
        payload = self._map(content)
        status, data = self._call("create", {"slug": content.slug}, payload, idempotency_key)
        if status in (200, 201, 202):
            return self._stored(content.slug, data, execution_id, idempotency_key, content.content_hash())
        if status == 409:
            raise DuplicateSlugError(f"The backend already has a '{self.capability.name}' at {content.slug!r}")
        raise _rejection(self.capability.name, status, data)

    def update(self, existing: StoredContent, content: Any, *, execution_id: str,
               idempotency_key: str) -> StoredContent:
        payload = self._map(content)
        status, data = self._call("update", {"slug": existing.slug, "id": existing.resource_id}, payload,
                                  idempotency_key)
        if status in (200, 201, 202, 204):
            return self._stored(existing.slug, data if status != 204 else {"id": existing.resource_id},
                                execution_id, idempotency_key, content.content_hash(), existing.resource_id)
        if status in (404, 410):
            raise TargetNotFoundError(f"The backend no longer has this '{self.capability.name}'")
        raise _rejection(self.capability.name, status, data)

    def validate(self, content: Any) -> None:
        """Dry run (verification): the content maps to the schema, and the
        backend answers a read when it declares one."""
        self._map(content)
        if "retrieve" in self.capability.operations and self.capability.identity == "slug":
            status, _ = self._call("retrieve", {"slug": content.slug, "id": ""})
            if status not in (200, 404):
                raise ContentStoreError(f"The backend answered HTTP {status} to a read")

    # Existing records (content inventory and record updates) -----------

    def supports_records(self) -> bool:
        """The backend lists its records, returns one, and updates one."""
        ops = self.capability.operations
        return "list" in ops and "retrieve" in ops and ("patch" in ops or "update" in ops)

    def _values(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """A record's values for the type's schema fields (WordPress
        {raw, rendered} objects as their raw text; long text cut)."""
        out: Dict[str, Any] = {}
        for spec in self.capability.fields:
            if spec.name not in record:
                continue
            value = record[spec.name]
            if isinstance(value, dict) and ("raw" in value or "rendered" in value):
                value = value.get("raw") if value.get("raw") is not None else value.get("rendered")
            if isinstance(value, str) and len(value) > RECORD_TEXT_MAX:
                value = value[:RECORD_TEXT_MAX]
            out[spec.name] = value
        return out

    def _summary(self, record: Dict[str, Any]) -> Dict[str, Any]:
        rid = record.get("id") if record.get("id") is not None else record.get("uuid") or record.get("_id")
        slug = record.get("slug") or record.get("handle")
        url = _rendered(record.get("url") or record.get("link") or record.get("permalink")
                        or record.get("public_url")) or ""
        if not url and self.capability.public and self.capability.public_url_template and (slug or rid):
            url = self.capability.public_url_template.replace("{slug}", str(slug or "")).replace("{id}", str(rid))
        return {"resource_id": str(rid) if rid is not None else None, "slug": slug,
                "url": url if self.capability.public else "",
                "updated_at": record.get("updated_at") or record.get("modified_gmt") or record.get("modified"),
                "values": self._values(record)}

    def list_records(self, *, page: int = 1, limit: int = 50) -> Tuple[List[Dict[str, Any]], bool]:
        """One page of the backend's existing records: ([summary], more)."""
        paging = self.capability.workflow.get("pagination") or {}
        query = {paging.get("page", "page"): page, paging.get("limit", "per_page"): limit}
        if self.capability.adapter == "wordpress":
            query["context"] = "edit"
        status, data = self._call("list", {}, query=query)
        if status == 400 and self.capability.adapter == "wordpress":   # past the last page
            return [], False
        if status != 200:
            raise ContentStoreError(f"The backend answered HTTP {status} to a list of '{self.capability.name}'")
        items = data if isinstance(data, list) else next(
            (data[k] for k in ("data", "items", "results", "records") if isinstance(data, dict)
             and isinstance(data.get(k), list)), [])
        meta = data.get("meta") if isinstance(data, dict) and isinstance(data.get("meta"), dict) else {}
        links = data.get("links") if isinstance(data, dict) and isinstance(data.get("links"), dict) else {}
        if meta.get("last_page") is not None and meta.get("current_page") is not None:
            more = int(meta["current_page"]) < int(meta["last_page"])
        elif "next" in links:
            more = bool(links.get("next"))
        else:
            more = len(items) >= limit
        return [self._summary(i) for i in items if isinstance(i, dict)], more

    def _key(self, resource_id: Optional[str], slug: Optional[str]) -> Dict[str, str]:
        key = {"id": str(resource_id or ""), "slug": str(slug or "")}
        if not key[self.capability.identity]:
            raise ContractViolationError(f"'{self.capability.name}' records are addressed by "
                                         f"{self.capability.identity}; none was given")
        return key

    def retrieve_record(self, *, resource_id: Optional[str], slug: Optional[str]) -> Optional[Dict[str, Any]]:
        status, data = self._call("retrieve", self._key(resource_id, slug),
                                  query={"context": "edit"} if self.capability.adapter == "wordpress" else None)
        if status in (404, 410):
            return None
        if status != 200:
            raise ContentStoreError(f"The backend answered HTTP {status} to a read of '{self.capability.name}'")
        return _unwrap(data)

    def update_record(self, *, resource_id: Optional[str], slug: Optional[str], changes: Dict[str, Any],
                      expected: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        """Change only *changes* on an existing record, if it still holds
        *expected* (no concurrent edit is overwritten). Returns the previous
        and current values of the changed fields."""
        from tekjuice_connector.publication.errors import RecordChangedError
        from tekjuice_connector.publication.schema_mapping import _coerce

        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tekjuice_record_changes (
                idempotency_key TEXT PRIMARY KEY, content_type TEXT NOT NULL, result TEXT NOT NULL,
                created_at TEXT NOT NULL)""")
            done = db.execute("SELECT result FROM tekjuice_record_changes WHERE idempotency_key = ?",
                              (idempotency_key,)).fetchone()
        if done is not None:
            return {**json.loads(done["result"]), "replayed": True}
        record = self.retrieve_record(resource_id=resource_id, slug=slug)
        if record is None:
            raise TargetNotFoundError(f"The backend has no such '{self.capability.name}'")
        current = self._values(record)
        changed = [k for k, v in expected.items() if _same(current.get(k), v) is False]
        if changed:
            raise RecordChangedError(f"The '{self.capability.name}' changed since it was assessed "
                                     f"({', '.join(sorted(changed))}); nothing was written", fields=changed)
        by_name = {spec.name: spec for spec in self.capability.fields}
        fitted: Dict[str, Any] = {}
        problems = []
        for name, value in changes.items():
            spec = by_name.get(name)
            if spec is None or spec.read_only:
                problems.append(f"'{name}' is not a writable field of '{self.capability.name}'")
                continue
            value, error = _coerce(spec, value, "fields")
            if error:
                problems.append(error)
            else:
                fitted[name] = value
        if problems or not fitted:
            raise ContentMappingError(f"The changes do not fit '{self.capability.name}': "
                                      + ("; ".join(problems) or "no changes"),
                                      report={"ok": False, "errors": problems})
        if "patch" in self.capability.operations:
            operation, body = "patch", fitted
        elif self.capability.adapter == "wordpress" or self.capability.operations["update"].method == "PATCH":
            operation, body = "update", fitted       # partial updates accepted
        else:                                        # PUT: the whole writable record with the changes
            operation = "update"
            body = {spec.name: current[spec.name] for spec in self.capability.fields
                    if not spec.read_only and spec.name in current and current[spec.name] is not None}
            body.update(fitted)
        key = self._key(resource_id, slug)
        status, data = self._call(operation, key, body, idempotency_key)
        if status in (404, 410):
            raise TargetNotFoundError(f"The backend no longer has this '{self.capability.name}'")
        if status not in (200, 201, 202, 204):
            raise _rejection(self.capability.name, status, data)
        after = self._values(_unwrap(data)) if status != 204 and isinstance(data, dict) else {}
        result = {"status": "applied", "content_type": self.capability.name,
                  "resource_id": str(resource_id or _unwrap(data).get("id") or ""), "slug": slug,
                  "url": self._summary(_unwrap(data) if isinstance(data, dict) else record)["url"],
                  "previous": {k: current.get(k) for k in fitted},
                  "current": {k: after.get(k, fitted[k]) for k in fitted}, "replayed": False}
        with self._lock, self._db() as db:
            db.execute("INSERT OR REPLACE INTO tekjuice_record_changes VALUES (?, ?, ?, ?)",
                       (idempotency_key, self.capability.name, json.dumps(result),
                        datetime.now(timezone.utc).isoformat()))
        return result

    def __repr__(self) -> str:
        return f"RestResourceStore(type={self.capability.name!r}, base_url={self._base!r})"


def _same(a: Any, b: Any) -> Optional[bool]:
    """Whether two record values are equal (numbers and text normalised)."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return float(a) == float(b)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    return a == b


def _rejection(name: str, status: int, data: Any) -> PublicationError:
    detail = f"HTTP {status}"
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, dict):   # Laravel / Rails style {"field": ["message"]}
            detail = "; ".join(f"{k}: {v[0] if isinstance(v, list) and v else v}" for k, v in list(errors.items())[:5])
        elif isinstance(errors, list):
            detail = "; ".join(str(e) for e in errors[:5])
        elif data.get("message"):
            detail = str(data["message"])[:300]
    return ContractViolationError(f"The backend rejected the '{name}': {detail}")


# ── Discovery sources ────────────────────────────────────────────────────────

def _named(fn: Callable, name: str) -> Callable:
    fn.name = name  # type: ignore[attr-defined]
    return fn


def native_source(store: Any, *, name: str = "article", label: str = "Article") -> Callable:
    """The backend's own ContentStore as one content type."""
    def discover() -> List[Tuple[ContentTypeCapability, Any]]:
        operations: Dict[str, Optional[Endpoint]] = {}
        if callable(getattr(store, "create", None)):
            operations["create"] = None
        if callable(getattr(store, "update", None)):
            operations["update"] = None
        fields = [FieldSpec("title", "string", required=True, nullable=False),
                  FieldSpec("body", "rich_text", required=True, nullable=False),
                  FieldSpec("slug", "slug", required=True, nullable=False),
                  FieldSpec("excerpt", "string"), FieldSpec("meta_description", "string"),
                  FieldSpec("meta_keywords", "array", items=FieldSpec("item", "string"))]
        capability = ContentTypeCapability(
            name=name, label=label, adapter="native", fields=fields, operations=operations,
            category=classify(name, label), identity="slug", public=True,
            public_url_template=_template_of(store), authorized=True,
            evidence=[f"the backend's integrated content store ({type(store).__name__})"])
        return [(capability, store)]
    return _named(discover, "native")


def _template_of(store: Any) -> Optional[str]:
    try:
        url = store.public_url("tekjuice-slug-probe")
    except Exception:  # noqa: BLE001
        return None
    return url.replace("tekjuice-slug-probe", "{slug}") if "tekjuice-slug-probe" in url else None


def _operations(spec: Any) -> Dict[str, Optional[Endpoint]]:
    out: Dict[str, Optional[Endpoint]] = {}
    for operation, endpoint in (spec or {}).items():
        if isinstance(endpoint, dict) and endpoint.get("path"):
            out[str(operation)] = Endpoint(method=str(endpoint.get("method") or "POST").upper(),
                                           path=safe_path(endpoint["path"]))
        elif endpoint is True:
            out[str(operation)] = None
    return out


def capability_from_manifest(entry: Dict[str, Any], *, adapter: str, evidence: str) -> ContentTypeCapability:
    """A content type as a backend's manifest declares it (content API)."""
    name = normalise_name(str(entry.get("name") or ""))
    label = str(entry.get("label") or entry.get("name") or name)
    schema = entry.get("schema") if isinstance(entry.get("schema"), dict) else {}
    capability = ContentTypeCapability(
        name=name, label=label, adapter=adapter, fields=fields_from_json_schema(schema),
        operations=_operations(entry.get("operations")), category=entry.get("category") or classify(name, label),
        identity=str(entry.get("identity") or "slug"), public=entry.get("public", True) is not False,
        public_url_template=entry.get("public_url_template"),
        workflow=entry.get("workflow") if isinstance(entry.get("workflow"), dict) else {},
        field_map={str(k): str(v) for k, v in (entry.get("field_map") or {}).items()},
        authorized=entry.get("enabled", True) is not False, evidence=[evidence])
    if entry.get("enabled") is False:
        capability.limitations.append("disabled in the backend's manifest")
    return capability


def content_api_source(base_url: str, *, ledger_path: str, token: Optional[str] = None,
                       request: Optional[Request] = None, timeout: float = 20.0) -> Callable:
    """Content types the sidecar content API lists in GET {base}/capabilities."""
    call = request or _requests_call
    auth = f"Bearer {token}" if token else None

    def discover() -> List[Tuple[ContentTypeCapability, Any]]:
        base = check_base_url(base_url, authenticated=bool(token))
        headers = {"Accept": "application/json", **({"Authorization": auth} if auth else {})}
        status, data = call("GET", base + "/capabilities", headers, None, timeout)
        if status in (401, 403):
            raise ContentSourceError(f"The content API refused the credential (HTTP {status})")
        if status != 200 or not isinstance(data, dict):
            return []
        out = []
        for entry in data.get("content_types") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            capability = capability_from_manifest(entry, adapter="content_api",
                                                  evidence="listed by the content API manifest (GET /capabilities)")
            out.append((capability, RestResourceStore(capability, base_url=base, ledger_path=ledger_path,
                                                      auth_header=auth, request=call, timeout=timeout)))
        return out
    return _named(discover, "content_api")


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ses") or word.endswith("xes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def capabilities_from_openapi(document: Dict[str, Any], *, adapter: str = "openapi") -> List[ContentTypeCapability]:
    """Content types an OpenAPI 3 document describes: a collection path with
    a JSON create operation, plus the item path's update/retrieve/delete."""
    paths = document.get("paths") if isinstance(document.get("paths"), dict) else {}
    info = document.get("info") or {}
    found: Dict[str, ContentTypeCapability] = {}
    for path, item in paths.items():
        if not isinstance(item, dict) or "{" in path.rstrip("/").rsplit("/", 1)[-1]:
            continue
        post = item.get("post")
        if not isinstance(post, dict):
            continue
        body = ((post.get("requestBody") or {}).get("content") or {}).get("application/json") or {}
        schema = body.get("schema") if isinstance(body.get("schema"), dict) else None
        if schema is None:
            continue
        extension = post.get("x-tekjuice-content-type")
        segment = [s for s in path.split("/") if s and "{" not in s][-1:] or ["content"]
        name = normalise_name(extension.get("name") if isinstance(extension, dict) and extension.get("name")
                              else _singular(segment[0]))
        label = (extension.get("label") if isinstance(extension, dict) else None) or post.get("summary") or name
        operations: Dict[str, Optional[Endpoint]] = {"create": Endpoint("POST", safe_path(path))}
        if isinstance(item.get("get"), dict):
            operations["list"] = Endpoint("GET", safe_path(path))
        identity = "id"
        for other, other_item in paths.items():
            match = re.fullmatch(re.escape(path.rstrip("/")) + r"/\{([a-zA-Z_][a-zA-Z0-9_]*)\}", other)
            if not match or not isinstance(other_item, dict):
                continue
            placeholder = match.group(1)
            identity = "slug" if "slug" in placeholder.lower() else "id"
            canonical = re.sub(r"\{[^}]+\}", "{" + identity + "}", other)
            for method, operation in (("put", "update"), ("patch", "update"), ("get", "retrieve"),
                                      ("delete", "delete")):
                if isinstance(other_item.get(method), dict) and operation not in operations:
                    operations[operation] = Endpoint(method.upper(), safe_path(canonical))
            if isinstance(other_item.get("patch"), dict) and operations.get("update") is not None \
                    and operations["update"].method != "PATCH":
                operations["patch"] = Endpoint("PATCH", safe_path(canonical))
        for other, other_item in paths.items():   # extra actions: /posts/{id}/publish, /issues/{id}/send
            match = re.fullmatch(re.escape(path.rstrip("/")) + r"/\{[^}]+\}/([a-z_-]+)", other)
            if match and isinstance(other_item, dict) and isinstance(other_item.get("post"), dict):
                action = match.group(1).replace("-", "_")
                if action not in operations:
                    operations[action] = Endpoint("POST", safe_path(re.sub(r"\{[^}]+\}", "{id}", other)))
        capability = ContentTypeCapability(
            name=name, label=str(label), adapter=adapter, fields=fields_from_json_schema(schema, document),
            operations=operations, category=classify(name, str(label)), identity=identity,
            public=not (isinstance(extension, dict) and extension.get("public") is False),
            public_url_template=extension.get("public_url_template") if isinstance(extension, dict) else None,
            workflow=extension.get("workflow") if isinstance(extension, dict) and isinstance(
                extension.get("workflow"), dict) else {},
            field_map={str(k): str(v) for k, v in ((extension or {}).get("field_map") or {}).items()}
            if isinstance(extension, dict) else {},
            authorized=bool(extension) and not (isinstance(extension, dict) and extension.get("enabled") is False),
            evidence=[f"OpenAPI {info.get('title') or ''} {info.get('version') or ''}: POST {path}".strip()])
        if bool(extension):
            capability.evidence.append("enabled by x-tekjuice-content-type")
        if name in found:
            capability.limitations.append(f"also described by another path; using {path}")
        found[name] = capability
    return list(found.values())


def openapi_source(base_url: str, *, document: Optional[Dict[str, Any]] = None, document_path: str = "/openapi.json",
                   ledger_path: str, token: Optional[str] = None, request: Optional[Request] = None,
                   timeout: float = 20.0) -> Callable:
    """Content types from the backend's OpenAPI document (fetched from the
    backend itself, at *document_path*, unless given)."""
    call = request or _requests_call
    auth = f"Bearer {token}" if token else None

    def discover() -> List[Tuple[ContentTypeCapability, Any]]:
        base = check_base_url(base_url, authenticated=bool(token))
        doc = document
        if doc is None:
            headers = {"Accept": "application/json", **({"Authorization": auth} if auth else {})}
            status, doc = call("GET", base + safe_path(document_path), headers, None, timeout)
            if status in (401, 403):
                raise ContentSourceError(f"The backend refused the credential for its OpenAPI document "
                                         f"(HTTP {status})")
            if status != 200 or not isinstance(doc, dict):
                raise ContentSourceError(f"The OpenAPI document was not found (HTTP {status})")
        if not str(doc.get("openapi") or "").startswith("3"):
            raise ContentSourceError("Only OpenAPI 3 documents are supported")
        return [(c, RestResourceStore(c, base_url=base, ledger_path=ledger_path, auth_header=auth, request=call,
                                      timeout=timeout)) for c in capabilities_from_openapi(doc)]
    return _named(discover, "openapi")


def _wordpress_fields(schema: Dict[str, Any]) -> List[FieldSpec]:
    """WordPress describes title/content/excerpt as {raw, rendered} objects but
    accepts strings when writing."""
    fields = fields_from_json_schema(schema)
    for spec in fields:
        names = {p.name for p in spec.properties}
        if spec.type == "object" and "raw" in names:
            spec.type = "rich_text" if spec.name == "content" else "string"
            spec.properties = []
    return [f for f in fields if f.name not in ("id", "link", "guid", "modified", "modified_gmt")]


def wordpress_source(site_url: str, *, username: str, application_password: str, ledger_path: str,
                     request: Optional[Request] = None, timeout: float = 20.0,
                     exclude: Iterable[str] = WORDPRESS_INTERNAL_TYPES) -> Callable:
    """Post types from WordPress REST, written with an application password.
    Records are created as WordPress drafts unless the publication's fields
    set ``status`` (an allowed value of the schema)."""
    call = request or _requests_call
    token = base64.b64encode(f"{username}:{application_password}".encode()).decode()
    auth = f"Basic {token}"
    skip = set(exclude)

    def discover() -> List[Tuple[ContentTypeCapability, Any]]:
        base = check_base_url(site_url.rstrip("/") + "/wp-json", authenticated=True)
        headers = {"Accept": "application/json", "Authorization": auth}
        status, types = call("GET", base + "/wp/v2/types?context=edit", headers, None, timeout)
        if status in (401, 403):
            raise ContentSourceError(f"WordPress refused the application password (HTTP {status})")
        if status != 200 or not isinstance(types, dict):
            raise ContentSourceError(f"WordPress types were not readable (HTTP {status})")
        out = []
        for slug, info in types.items():
            if slug in skip or not isinstance(info, dict) or not info.get("rest_base"):
                continue
            namespace = info.get("rest_namespace") or "wp/v2"
            collection = f"/{namespace}/{info['rest_base']}"
            status, options = call("OPTIONS", base + safe_path(collection), headers, None, timeout)
            schema = options.get("schema") if status == 200 and isinstance(options, dict) else None
            capability = ContentTypeCapability(
                name=normalise_name(slug), label=str(info.get("name") or slug), adapter="wordpress",
                fields=_wordpress_fields(schema) if isinstance(schema, dict) else [],
                operations={"create": Endpoint("POST", safe_path(collection)),
                            "list": Endpoint("GET", safe_path(collection)),
                            "update": Endpoint("POST", safe_path(collection + "/{id}")),
                            "retrieve": Endpoint("GET", safe_path(collection + "/{id}")),
                            "delete": Endpoint("DELETE", safe_path(collection + "/{id}"))},
                category=classify(slug, str(info.get("name") or "")), identity="id",
                public=bool(info.get("viewable", True)),
                workflow={"states": ["draft", "pending", "private", "publish", "future"], "default": "draft"},
                evidence=[f"WordPress REST type '{slug}' ({collection})"])
            if capability.public:
                capability.limitations.append("created as a draft unless fields.status is set")
            out.append((capability, RestResourceStore(capability, base_url=base, ledger_path=ledger_path,
                                                      auth_header=auth, request=call, timeout=timeout)))
        return out
    return _named(discover, "wordpress")
