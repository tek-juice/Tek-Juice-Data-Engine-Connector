"""Site changes: approved fixes to the customer's own pages (``site.changes``).

The Data Engine may ask a receiver to override a page's head metadata or to
redirect a path, for problems its audits found on pages Tek Juice did not
publish (a canonical link pointing elsewhere, a noindex directive) or to
consolidate overlapping articles. The receiver advertises ``site.changes``
only when its store keeps overrides and the site serves pages through
``SeoPages`` (which applies them).

Request (contract 1.0, ``action: "site_change"``)::

    {"contract_version": "1.0", "action": "site_change", "website_id": "...",
     "idempotency_key": "...",
     "change": {"type": "page_meta", "path": "/resources",
                "title": "...", "meta_description": "...",
                "canonical": "https://example.com/resources", "robots": "index,follow"}}

    {"change": {"type": "redirect", "path": "/old", "target": "/new", "status_code": 301}}
    {"change": {"type": "remove", "path": "/old", "remove": "redirect" | "meta" | "sitemap" | "all"}}

With ``site.changes.sitemap`` (sitemaps served by ``SeoPages``)::

    {"change": {"type": "sitemap", "path": "/pricing", "sitemap": "include" | "exclude"}}

``include`` lists one of the site's own pages in the sitemap, ``exclude``
leaves a page out. No lastmod is invented for included pages: only real
modification dates are reported.

Rules: paths are absolute site paths; canonical URLs and redirect targets
stay on the site; redirects may not loop or chain (the target may not itself
redirect); a repeated idempotency key replays the earlier result. Every
response includes the previous override so the change can be rolled back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from tekjuice_connector.publication.errors import ContractViolationError

ACTION_SITE_CHANGE = "site_change"
CAPABILITY_SITE_CHANGES = "site.changes"
CAPABILITY_SITE_CHANGES_SITEMAP = "site.changes.sitemap"
CHANGE_TYPES = ("page_meta", "redirect", "remove", "sitemap")
SITEMAP_VALUES = ("include", "exclude")
ROBOTS_VALUES = ("index,follow", "noindex,follow", "index,nofollow", "noindex,nofollow")
REDIRECT_CODES = (301, 302, 307, 308)
MAX_PATH = 300
_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
_REQUEST_FIELDS = frozenset({"contract_version", "action", "website_id", "idempotency_key", "change"})
_CHANGE_FIELDS = frozenset({"type", "path", "title", "meta_description", "canonical", "robots", "target",
                            "status_code", "remove", "sitemap"})


@dataclass(frozen=True)
class SiteChangeRequest:
    idempotency_key: str
    change: Dict[str, Any]


def normalize_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > MAX_PATH:
        raise ContractViolationError(f"'change.{field}' must be a site path starting with '/'")
    if "//" in value or "/../" in value or value.endswith("/..") or not _PATH.match(value):
        raise ContractViolationError(f"'change.{field}' is not a valid site path")
    return value.rstrip("/") or "/"


def _same_site(value: str, base: str, field: str) -> str:
    """A path, or an absolute URL on the site; returned as a path."""
    if value.startswith("/"):
        return normalize_path(value, field)
    parts, site = urlsplit(value), urlsplit(base)
    if parts.scheme not in ("https", "http") or (parts.hostname or "").lower() != (site.hostname or "").lower():
        raise ContractViolationError(f"'change.{field}' must stay on {site.hostname}")
    return normalize_path(parts.path or "/", field)


def parse_site_change(data: Dict[str, Any], *, base_url: str) -> SiteChangeRequest:
    unknown = set(data) - _REQUEST_FIELDS
    if unknown:
        raise ContractViolationError(f"Unknown request field(s): {', '.join(sorted(unknown))}")
    key = data.get("idempotency_key")
    if not isinstance(key, str) or not 8 <= len(key) <= 300:
        raise ContractViolationError("'idempotency_key' is required")
    change = data.get("change")
    if not isinstance(change, dict):
        raise ContractViolationError("'change' must be an object")
    unknown = set(change) - _CHANGE_FIELDS
    if unknown:
        raise ContractViolationError(f"Unknown change field(s): {', '.join(sorted(unknown))}")
    kind = change.get("type")
    if kind not in CHANGE_TYPES:
        raise ContractViolationError(f"'change.type' must be one of {', '.join(CHANGE_TYPES)}")
    out: Dict[str, Any] = {"type": kind, "path": normalize_path(change.get("path"), "path")}
    if kind == "page_meta":
        meta: Dict[str, Any] = {}
        for field, limit in (("title", 120), ("meta_description", 320)):
            if change.get(field) is not None:
                if not isinstance(change[field], str) or not 0 < len(change[field].strip()) <= limit:
                    raise ContractViolationError(f"'change.{field}' must be text up to {limit} characters")
                meta[field] = change[field].strip()
        if change.get("canonical") is not None:
            canonical_path = _same_site(str(change["canonical"]), base_url, "canonical")
            meta["canonical"] = base_url.rstrip("/") + ("" if canonical_path == "/" else canonical_path) + (
                "/" if canonical_path == "/" else "")
        if change.get("robots") is not None:
            robots = str(change["robots"]).replace(" ", "").lower()
            if robots not in ROBOTS_VALUES:
                raise ContractViolationError(f"'change.robots' must be one of {', '.join(ROBOTS_VALUES)}")
            meta["robots"] = robots
        if not meta:
            raise ContractViolationError("'page_meta' needs at least one of title, meta_description, canonical, robots")
        out["meta"] = meta
    elif kind == "redirect":
        target = _same_site(str(change.get("target") or ""), base_url, "target")
        if target == out["path"]:
            raise ContractViolationError("a page cannot redirect to itself")
        code = change.get("status_code", 301)
        if code not in REDIRECT_CODES:
            raise ContractViolationError(f"'change.status_code' must be one of {REDIRECT_CODES}")
        out["redirect"] = {"target": target, "status_code": code}
    elif kind == "sitemap":
        value = change.get("sitemap")
        if value not in SITEMAP_VALUES:
            raise ContractViolationError("'change.sitemap' must be include or exclude")
        out["sitemap"] = value
    else:
        remove = change.get("remove", "all")
        if remove not in ("meta", "redirect", "sitemap", "all"):
            raise ContractViolationError("'change.remove' must be meta, redirect, sitemap or all")
        out["remove"] = remove
    return SiteChangeRequest(idempotency_key=key, change=out)


def apply_site_change(store: Any, request: SiteChangeRequest) -> Dict[str, Any]:
    """Apply to the store; idempotent; returns the response body."""
    change = request.change
    path = change["path"]
    previous: Optional[Dict[str, Any]] = store.get_site_override(path)
    if previous and previous.get("idempotency_key") == request.idempotency_key:
        return {"status": "applied", "replayed": True, "path": path,
                "previous": previous.get("previous_state"), "current": _public(previous)}
    current: Dict[str, Any] = {"meta": (previous or {}).get("meta"), "redirect": (previous or {}).get("redirect"),
                               "sitemap": (previous or {}).get("sitemap")}
    if change["type"] == "sitemap":
        current["sitemap"] = change["sitemap"]
    elif change["type"] == "page_meta":
        current["meta"] = {**(current["meta"] or {}), **change["meta"]}
    elif change["type"] == "redirect":
        target = change["redirect"]["target"]
        target_override = store.get_site_override(target) or {}
        if target_override.get("redirect"):
            raise ContractViolationError("the redirect target itself redirects (chains are not allowed)")
        sources = store.list_site_overrides()
        if any((o.get("redirect") or {}).get("target") == path for o in sources.values()):
            raise ContractViolationError("other paths redirect here; redirecting it would create a chain")
        current["redirect"] = change["redirect"]
    else:
        if change["remove"] in ("meta", "all"):
            current["meta"] = None
        if change["remove"] in ("redirect", "all"):
            current["redirect"] = None
        if change["remove"] in ("sitemap", "all"):
            current["sitemap"] = None
    stored = {**current, "idempotency_key": request.idempotency_key, "previous_state": _public(previous)}
    if not current["meta"] and not current["redirect"] and not current["sitemap"]:
        store.delete_site_override(path)
    else:
        store.set_site_override(path, stored)
    return {"status": "applied", "replayed": False, "path": path, "previous": _public(previous),
            "current": _public(stored)}


def _public(override: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not override:
        return None
    return {"meta": override.get("meta"), "redirect": override.get("redirect"),
            "sitemap": override.get("sitemap")}


def store_supports_site_changes(store: Any) -> bool:
    return all(callable(getattr(store, m, None)) for m in
               ("get_site_override", "set_site_override", "delete_site_override", "list_site_overrides"))
