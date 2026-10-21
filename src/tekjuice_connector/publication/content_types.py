"""Content types a customer backend supports: the runtime capability map.

A backend may support one kind of content (the classic article store) or
many (blog posts, products, events, newsletter issues, records only that
application knows). Nothing here is a fixed list: content types are
*discovered* from the backend (content_sources.py) and each becomes a
:class:`ContentTypeCapability` with

- its fields (normalised from the backend's own schema),
- the operations the backend exposes and the ones Tek Juice may perform
  (``create`` and ``update`` through the S2S contract; ``send``, ``delete``
  and others are recorded but never performed),
- how its public URL is formed and whether it is public at all,
- where the evidence came from and a status:

  ``discovered``  the backend lists it;
  ``validated``   its schema was read and a publication can be mapped to it;
  ``authorized``  the backend owner enabled it for Tek Juice (an explicit
                  manifest entry, an ``x-tekjuice-content-type`` extension or
                  the ``TEKJUICE_CONTENT_TYPES`` allow-list);
  ``ready``       validated, authorized and a create (or update) operation
                  is available: Tek Juice may publish to it;
  ``unsupported`` it cannot be used, with the reasons.

A route existing is never enough for ``ready``.

The taxonomy below only helps describe and classify types (and keeps an
article from being routed to a product); a type absent from it is still
supported when its schema is usable.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

DISCOVERED = "discovered"
VALIDATED = "validated"
AUTHORIZED = "authorized"
READY = "ready"
UNSUPPORTED = "unsupported"
STATUSES = (DISCOVERED, VALIDATED, AUTHORIZED, READY, UNSUPPORTED)

# Operations a backend may expose. Only these go through the S2S contract.
EXECUTABLE_OPERATIONS = ("create", "update")
KNOWN_OPERATIONS = ("create", "update", "retrieve", "list", "publish", "unpublish", "delete", "send",
                    "schedule", "archive", "upload_media")

DEFAULT_TYPE = "article"
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
DEFAULT_TTL_SECONDS = 900.0

# Field types after normalisation.
FIELD_TYPES = ("string", "text", "rich_text", "slug", "url", "email", "integer", "number", "boolean", "date",
               "datetime", "enum", "reference", "media", "object", "array", "json")

# Discovery aid: category -> words that name content types of that kind.
TAXONOMY: Dict[str, Tuple[str, ...]] = {
    "editorial": ("blog", "post", "article", "news", "story", "editorial", "opinion", "feature", "interview",
                  "report", "press", "release", "announcement", "essay", "column", "biography", "obituary",
                  "whitepaper", "paper", "casestudy", "case_study", "publication", "insight", "guide"),
    "newsletter": ("newsletter", "issue", "campaign", "digest", "bulletin", "mailing"),
    "commerce": ("product", "variant", "bundle", "sku", "service", "subscription", "membership", "plan",
                 "pricing", "offer", "discount", "promotion", "coupon", "deal", "giftcard", "listing", "inventory",
                 "review", "comparison", "merchant"),
    "travel_local": ("destination", "attraction", "itinerary", "tour", "package", "activity", "hotel",
                     "accommodation", "room", "amenity", "restaurant", "venue", "route", "provider", "business",
                     "directory", "booking", "availability"),
    "education": ("course", "programme", "program", "module", "lesson", "tutorial", "path", "assignment", "quiz",
                  "exam", "examination", "resource", "certificate", "instructor", "department", "research"),
    "media": ("image", "gallery", "video", "livestream", "audio", "podcast", "episode", "music", "track",
              "playlist", "transcript", "subtitle", "caption", "document", "download", "presentation",
              "portfolio", "asset", "media", "file"),
    "documentation": ("doc", "docs", "documentation", "reference", "faq", "help", "kb", "knowledge", "knowledgebase",
                      "troubleshooting", "manual", "policy", "glossary", "specification", "spec", "changelog",
                      "releasenote", "release_note", "support"),
    "events": ("event", "conference", "webinar", "workshop", "festival", "exhibition", "performance",
               "appointment", "meeting", "class", "session", "ticket", "registration", "schedule", "calendar"),
    "business": ("job", "vacancy", "career", "position", "company", "staff", "team", "member", "partner",
                 "project", "tender", "procurement", "investor", "corporate"),
    "community": ("profile", "forum", "thread", "discussion", "comment", "reply", "testimonial", "rating",
                  "question", "answer", "poll", "survey", "submission"),
    "real_estate": ("property", "rental", "sale", "development", "building", "unit", "floorplan", "floor_plan",
                    "agent", "neighbourhood", "neighborhood"),
    "institutional": ("notice", "law", "regulation", "grant", "funding", "consultation", "dataset", "impact"),
    "structural": ("page", "landing", "homepage", "category", "taxonomy", "tag", "navigation", "menu", "block",
                   "section", "record", "entry", "item", "tool", "calculator"),
}
# Categories an editorial article may be published into.
ARTICLE_CATEGORIES = ("editorial",)


def classify(name: str, label: Optional[str] = None) -> Optional[str]:
    """The taxonomy category a content type name suggests, or None."""
    words = [w for w in re.split(r"[^a-z0-9]+", f"{name} {label or ''}".lower()) if w]
    singular = [w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words]
    for category, terms in TAXONOMY.items():
        if any(w in terms for w in words + singular):
            return category
    return None


def normalise_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9_.-]+", "_", (value or "").strip().lower()).strip("_")
    return name[:63] or "content"


@dataclass
class FieldSpec:
    """One destination field, normalised from the backend's schema."""

    name: str
    type: str = "string"
    required: bool = False
    nullable: bool = True
    enum: Optional[List[Any]] = None
    default: Any = None
    max_length: Optional[int] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    read_only: bool = False
    description: Optional[str] = None
    items: Optional["FieldSpec"] = None
    properties: List["FieldSpec"] = field(default_factory=list)
    reference_to: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "type": self.type, "required": self.required}
        for key in ("enum", "default", "max_length", "minimum", "maximum", "reference_to", "description"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if not self.nullable:
            out["nullable"] = False
        if self.read_only:
            out["read_only"] = True
        if self.items is not None:
            out["items"] = self.items.to_dict()
        if self.properties:
            out["properties"] = [p.to_dict() for p in self.properties]
        return out


@dataclass
class Endpoint:
    """How an operation is invoked: an HTTP method and a path relative to the
    backend's content API base (never another host)."""

    method: str
    path: str

    def to_dict(self) -> Dict[str, str]:
        return {"method": self.method, "path": self.path}


@dataclass
class ContentTypeCapability:
    name: str
    label: str
    adapter: str
    fields: List[FieldSpec] = field(default_factory=list)
    operations: Dict[str, Optional[Endpoint]] = field(default_factory=dict)  # available operations
    category: Optional[str] = None
    identity: str = "slug"                      # how a published record is addressed: slug or id
    public: bool = True                         # published records have a public page
    public_url_template: Optional[str] = None
    workflow: Dict[str, Any] = field(default_factory=dict)  # e.g. {"states": [...], "default": "draft"}
    field_map: Dict[str, str] = field(default_factory=dict)  # explicit destination -> source mapping
    authorized: bool = False
    evidence: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    status: str = DISCOVERED
    validated_at: Optional[float] = None

    @property
    def schema_version(self) -> str:
        material = json.dumps({"fields": [f.to_dict() for f in self.fields],
                               "operations": {k: v.to_dict() if v else None for k, v in self.operations.items()},
                               "public": self.public, "identity": self.identity}, sort_keys=True, default=str)
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    def executable(self, operation: str) -> bool:
        return operation in EXECUTABLE_OPERATIONS and operation in self.operations

    def to_dict(self) -> Dict[str, Any]:
        """The public description (no credentials, no base URLs)."""
        return {
            "name": self.name, "label": self.label, "category": self.category, "adapter": self.adapter,
            "status": self.status, "public": self.public, "identity": self.identity,
            "public_url_template": self.public_url_template,
            "operations": sorted(self.operations),
            "executable_operations": sorted(o for o in self.operations if o in EXECUTABLE_OPERATIONS),
            "fields": [f.to_dict() for f in self.fields],
            "required_fields": [f.name for f in self.fields if f.required and not f.read_only],
            "workflow": self.workflow, "field_map": self.field_map, "schema_version": self.schema_version,
            "evidence": list(self.evidence), "limitations": list(self.limitations),
        }


class ContentTypeError(Exception):
    """A publication names a content type the receiver cannot use."""

    def __init__(self, message: str, *, content_type: str, reasons: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.content_type = content_type
        self.reasons = list(reasons)


Source = Callable[[], List[Tuple[ContentTypeCapability, Any]]]


class ContentTypeRegistry:
    """The capability map of one receiver (one website): content type ->
    (capability, store). Sources are re-read after *ttl* seconds or on
    :meth:`refresh`; a type whose schema changed is validated again."""

    def __init__(self, sources: Sequence[Source], *, default: Optional[str] = DEFAULT_TYPE,
                 allow: Optional[Iterable[str]] = None, ttl: float = DEFAULT_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._sources = list(sources)
        self._default = default
        self._allow = {normalise_name(a) for a in allow} if allow else set()
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._types: Dict[str, Tuple[ContentTypeCapability, Any]] = {}
        self._loaded_at: Optional[float] = None
        self._errors: List[str] = []

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        from tekjuice_connector.publication.schema_mapping import probe

        found: Dict[str, Tuple[ContentTypeCapability, Any]] = {}
        errors: List[str] = []
        for source in self._sources:
            try:
                entries = source()
            except Exception as exc:  # noqa: BLE001 - one source failing must not hide the others
                errors.append(f"{getattr(source, 'name', type(source).__name__)}: {type(exc).__name__}: {exc}"[:300])
                continue
            for capability, store in entries:
                if capability.name in found:
                    capability.status = UNSUPPORTED
                    capability.limitations.append(
                        f"another source already provides a content type named '{capability.name}'")
                    errors.append(f"duplicate content type '{capability.name}' ({capability.adapter})")
                    continue
                found[capability.name] = (capability, store)
        now = self._clock()
        previous = self._types
        for name, (capability, store) in found.items():
            if capability.status == UNSUPPORTED:
                continue
            if self._allow and name in self._allow and not capability.authorized:
                capability.authorized = True
                capability.evidence.append("enabled by TEKJUICE_CONTENT_TYPES")
            old = previous.get(name)
            if old is not None and old[0].schema_version != capability.schema_version:
                capability.evidence.append(f"schema changed (was {old[0].schema_version}); validated again")
            problems = probe(capability)
            if problems:
                capability.status = UNSUPPORTED
                capability.limitations.extend(problems)
                continue
            capability.validated_at = now
            if not capability.authorized:
                capability.status = VALIDATED
                capability.limitations.append(
                    "not enabled for Tek Juice: list it in the content API manifest, mark the operation with "
                    "x-tekjuice-content-type, or add it to TEKJUICE_CONTENT_TYPES")
            elif not any(capability.executable(op) for op in EXECUTABLE_OPERATIONS):
                capability.status = AUTHORIZED
                capability.limitations.append("no create or update operation is available")
            else:
                capability.status = READY
        with self._lock:
            self._types = found
            self._errors = errors
            self._loaded_at = now

    def _ensure(self) -> None:
        if self._loaded_at is None or self._clock() - self._loaded_at > self._ttl:
            self.refresh()

    # ------------------------------------------------------------------

    @property
    def default(self) -> Optional[str]:
        return self._default

    def names(self) -> List[str]:
        self._ensure()
        return sorted(self._types)

    def get(self, name: str) -> Optional[ContentTypeCapability]:
        self._ensure()
        entry = self._types.get(name)
        return entry[0] if entry else None

    def resolve(self, name: Optional[str], operation: str) -> Tuple[ContentTypeCapability, Any]:
        """(capability, store) for a publication; raises ContentTypeError with
        the missing evidence when the type cannot be used for *operation*."""
        self._ensure()
        name = name or self._default
        if not name:
            raise ContentTypeError("No content type was named and this receiver has no default",
                                   content_type="")
        entry = self._types.get(name)
        if entry is None:
            known = ", ".join(sorted(self._types)) or "none"
            raise ContentTypeError(f"Unknown content type '{name}' (this backend supports: {known})",
                                   content_type=name)
        capability, store = entry
        if capability.status != READY:
            raise ContentTypeError(f"Content type '{name}' is {capability.status}, not ready",
                                   content_type=name, reasons=capability.limitations)
        if not capability.executable(operation):
            raise ContentTypeError(f"Content type '{name}' does not support '{operation}'",
                                   content_type=name, reasons=[f"available: {sorted(capability.operations)}"])
        return capability, store

    def records(self, name: str) -> Tuple[ContentTypeCapability, Any]:
        """(capability, store) for reading and improving a type's existing
        records (content inventory, record updates)."""
        self._ensure()
        entry = self._types.get(name)
        if entry is None:
            raise ContentTypeError(f"Unknown content type '{name}'", content_type=name)
        capability, store = entry
        if capability.status != READY:
            raise ContentTypeError(f"Content type '{name}' is {capability.status}, not ready",
                                   content_type=name, reasons=capability.limitations)
        supports = getattr(store, "supports_records", None)
        if not callable(supports) or not supports():
            raise ContentTypeError(f"Content type '{name}' has no list, read and update of existing records",
                                   content_type=name, reasons=[f"available: {sorted(capability.operations)}"])
        return capability, store

    def describe(self) -> Dict[str, Any]:
        """The capability map for the ``capabilities`` action and diagnostics."""
        self._ensure()
        types = []
        for _, (capability, store) in sorted(self._types.items()):
            item = capability.to_dict()
            supports = getattr(store, "supports_records", None)
            item["records"] = bool(capability.status == READY and callable(supports) and supports())
            types.append(item)
        return {"default": self._default if self._default in self._types else None,
                "types": types, "ready": [t["name"] for t in types if t["status"] == READY],
                "errors": list(self._errors), "ttl_seconds": self._ttl}
