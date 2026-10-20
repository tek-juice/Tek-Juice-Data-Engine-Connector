"""Backend schemas -> fields, and a publication -> a backend payload.

``fields_from_json_schema`` normalises a JSON Schema (also the schemas inside
OpenAPI documents and WordPress REST) into :class:`FieldSpec` objects.

``map_publication`` builds the payload for one content type from a
publication: the standard content (title, body, slug, excerpt, meta
description, keywords) and the publication's own ``fields`` (structured
values for types that are not articles: a price, an event date, a product
SKU). Explicit values win; the content type's ``field_map`` comes next; then
semantic equivalents (``headline`` for the title, ``summary`` for the
excerpt). Values are checked against the destination schema (type, enum,
length, required) and nothing is invented: a required field nobody supplied,
a value of the wrong type or a destination that two source values compete
for is reported, and the publication is refused rather than guessed.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from tekjuice_connector.publication.content_types import ContentTypeCapability, FieldSpec

MAX_DEPTH = 6

# Semantic roles of the standard publication values -> destination field
# names that mean the same (normalised: lower case, no separators).
ROLES: Dict[str, Tuple[str, ...]] = {
    "title": ("title", "name", "headline", "subject", "heading", "posttitle"),
    "body": ("body", "content", "html", "bodyhtml", "contenthtml", "articlebody", "postcontent", "text",
             "richtext", "description"),
    "slug": ("slug", "handle", "permalink", "urlslug", "path", "urlkey"),
    "excerpt": ("excerpt", "summary", "teaser", "intro", "lead", "standfirst", "abstract", "subtitle",
                "shortdescription"),
    "meta_description": ("metadescription", "seodescription", "descriptionseo", "metadesc"),
    "meta_keywords": ("metakeywords", "keywords", "tags", "seokeywords"),
}
# A destination named like this takes the role only when no stronger match exists.
WEAK = {("body", "description"), ("body", "text"), ("slug", "path"), ("excerpt", "subtitle")}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


# ── Schema normalisation ─────────────────────────────────────────────────────

def _resolve(schema: Dict[str, Any], root: Dict[str, Any], depth: int = 0) -> Dict[str, Any]:
    while isinstance(schema, dict) and "$ref" in schema and depth < MAX_DEPTH:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return {}
        node: Any = root
        for part in ref[2:].split("/"):
            node = node.get(part.replace("~1", "/").replace("~0", "~")) if isinstance(node, dict) else None
        schema = node if isinstance(node, dict) else {}
        depth += 1
    for combinator in ("allOf",):
        if isinstance(schema.get(combinator), list):
            merged: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
            for part in schema[combinator]:
                part = _resolve(part, root, depth + 1)
                merged["properties"].update(part.get("properties") or {})
                merged["required"] += list(part.get("required") or [])
            schema = {**{k: v for k, v in schema.items() if k != combinator}, **merged}
    return schema


def _field_type(name: str, schema: Dict[str, Any]) -> str:
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), None)
    fmt = (schema.get("format") or "").lower()
    if schema.get("enum"):
        return "enum"
    if schema.get("x-tekjuice-type") in ("rich_text", "media", "reference", "slug", "text"):
        return schema["x-tekjuice-type"]
    if kind == "integer":
        return "reference" if _norm(name).endswith("id") and _norm(name) != "id" else "integer"
    if kind == "number":
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "array":
        return "array"
    if kind == "object":
        return "object"
    if fmt == "date":
        return "date"
    if fmt in ("date-time", "datetime"):
        return "datetime"
    if fmt in ("uri", "url", "iri"):
        return "media" if any(w in _norm(name) for w in ("image", "photo", "thumbnail", "cover", "logo")) else "url"
    if fmt == "email":
        return "email"
    if fmt in ("html", "rich-text", "richtext") or schema.get("contentMediaType") == "text/html":
        return "rich_text"
    if kind == "string" or kind is None:
        if _norm(name) in ("slug", "handle", "permalink"):
            return "slug"
        return "string"
    return "json"


def fields_from_json_schema(schema: Dict[str, Any], root: Optional[Dict[str, Any]] = None,
                            depth: int = 0) -> List[FieldSpec]:
    """FieldSpecs of an object schema (nested objects and arrays included)."""
    root = root if root is not None else schema
    schema = _resolve(schema, root)
    required = set(schema.get("required") or [])
    out: List[FieldSpec] = []
    for name, prop in (schema.get("properties") or {}).items():
        prop = _resolve(prop if isinstance(prop, dict) else {}, root)
        kind = _field_type(name, prop)
        types = prop.get("type") if isinstance(prop.get("type"), list) else [prop.get("type")]
        spec = FieldSpec(
            name=name, type=kind, required=name in required,
            nullable="null" in types or bool(prop.get("nullable")),
            enum=list(prop["enum"]) if prop.get("enum") else None, default=prop.get("default"),
            max_length=prop.get("maxLength"), minimum=prop.get("minimum"), maximum=prop.get("maximum"),
            read_only=bool(prop.get("readOnly")), description=(prop.get("description") or None),
            reference_to=prop.get("x-tekjuice-reference"),
        )
        if kind == "object" and depth < MAX_DEPTH:
            spec.properties = fields_from_json_schema(prop, root, depth + 1)
        if kind == "array" and isinstance(prop.get("items"), dict) and depth < MAX_DEPTH:
            item = _resolve(prop["items"], root)
            spec.items = FieldSpec(name="item", type=_field_type(name, item))
            if spec.items.type == "object":
                spec.items.properties = fields_from_json_schema(item, root, depth + 1)
        out.append(spec)
    return out


def probe(capability: ContentTypeCapability) -> List[str]:
    """Structural problems that make a content type unusable (empty when fine)."""
    problems = []
    if capability.adapter != "native" and not capability.fields:
        problems.append("the backend did not describe this type's fields (no schema)")
    for spec in capability.fields:
        if spec.required and spec.read_only:
            problems.append(f"field '{spec.name}' is both required and read-only")
        if spec.type == "enum" and spec.required and not spec.enum:
            problems.append(f"required enum field '{spec.name}' lists no values")
    if capability.identity not in ("slug", "id"):
        problems.append(f"unknown record identity '{capability.identity}'")
    if capability.identity == "slug" and capability.fields and not any(
            spec.type == "slug" or _norm(spec.name) in ROLES["slug"] for spec in capability.fields):
        problems.append("records are addressed by slug but the schema has no slug field")
    for name in capability.field_map:
        if not any(spec.name == name for spec in capability.fields):
            problems.append(f"field_map names unknown field '{name}'")
    return problems


# ── Mapping a publication ────────────────────────────────────────────────────

@dataclass
class MappingResult:
    payload: Dict[str, Any] = field(default_factory=dict)
    mapped: Dict[str, str] = field(default_factory=dict)          # destination -> source
    unmapped_sources: List[str] = field(default_factory=list)     # source values with no destination
    missing_required: List[str] = field(default_factory=list)
    ambiguous: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing_required or self.ambiguous or self.errors)

    def report(self) -> Dict[str, Any]:
        return {"ok": self.ok, "mapped": self.mapped, "unmapped_sources": self.unmapped_sources,
                "missing_required": self.missing_required, "ambiguous": self.ambiguous, "errors": self.errors}


def _standard_values(content: Any) -> Dict[str, Any]:
    values = {"title": content.title, "body": content.body, "slug": content.slug, "excerpt": content.excerpt,
              "meta_description": content.meta_description,
              "meta_keywords": list(content.meta_keywords) if content.meta_keywords else None}
    return {k: v for k, v in values.items() if v not in (None, "", [])}


# Destination field types each standard value can go into (a semantic name
# match alone is not enough: WordPress "tags" are term ids, not keywords).
ROLE_TYPES: Dict[str, Tuple[str, ...]] = {
    "title": ("string", "text"), "body": ("rich_text", "text", "string"), "slug": ("slug", "string"),
    "excerpt": ("string", "text", "rich_text"), "meta_description": ("string", "text"),
    "meta_keywords": ("array", "string"),
}


def _fits(role: str, spec: FieldSpec) -> bool:
    if spec.type not in ROLE_TYPES[role]:
        return False
    return not (spec.type == "array" and (spec.items is None or spec.items.type not in ("string", "text")))


def _role_candidates(fields: List[FieldSpec]) -> Dict[str, List[Tuple[FieldSpec, bool]]]:
    """role -> [(destination field, strong match)]"""
    out: Dict[str, List[Tuple[FieldSpec, bool]]] = {}
    for spec in fields:
        if spec.read_only:
            continue
        key = _norm(spec.name)
        for role, names in ROLES.items():
            if key in names and _fits(role, spec):
                out.setdefault(role, []).append((spec, (role, key) not in WEAK))
    return out


_BLOCK_TAG = re.compile(r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|td|th|table|blockquote|section|article|hr)\b[^>]*>", re.I)


def _strip_html(value: str) -> str:
    """Plain text of HTML: block elements separate words, inline ones do not."""
    text = re.sub(r"<[^>]+>", "", _BLOCK_TAG.sub(" ", value))
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _coerce(spec: FieldSpec, value: Any, source: str) -> Tuple[Any, Optional[str]]:
    """(value fitted to *spec*, error)"""
    if value is None:
        return None, None if spec.nullable or not spec.required else f"'{spec.name}' may not be null"
    kind = spec.type
    try:
        if kind in ("string", "slug", "url", "email", "text"):
            if not isinstance(value, str):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    value = str(value)
                else:
                    return None, f"'{spec.name}' expects text, got {type(value).__name__} from {source}"
            if source == "body" and kind in ("string", "text"):
                value = _strip_html(value)
        elif kind == "rich_text":
            if not isinstance(value, str):
                return None, f"'{spec.name}' expects rich text, got {type(value).__name__} from {source}"
        elif kind == "integer":
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                return None, f"'{spec.name}' expects an integer, got {value!r}"
            value = int(value)
        elif kind == "number":
            if isinstance(value, bool):
                return None, f"'{spec.name}' expects a number, got a boolean"
            value = float(value) if not isinstance(value, (int, float)) else value
        elif kind == "boolean":
            if not isinstance(value, bool):
                return None, f"'{spec.name}' expects true/false, got {value!r}"
        elif kind == "date":
            if not isinstance(value, str):
                return None, f"'{spec.name}' expects an ISO date (YYYY-MM-DD)"
            date.fromisoformat(value)
        elif kind == "datetime":
            if not isinstance(value, str):
                return None, f"'{spec.name}' expects an ISO date-time"
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None, f"'{spec.name}' needs a time zone (e.g. 2026-10-20T09:00:00+00:00)"
        elif kind == "enum":
            options = spec.enum or []
            if value not in options:
                folded = {str(o).lower(): o for o in options}
                if isinstance(value, str) and value.lower() in folded:
                    value = folded[value.lower()]
                else:
                    return None, f"'{spec.name}' must be one of {options}, got {value!r}"
        elif kind == "array":
            if source == "meta_keywords" and isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            if not isinstance(value, list):
                return None, f"'{spec.name}' expects a list, got {type(value).__name__}"
            if spec.items is not None and spec.items.type in ("string", "integer", "number", "boolean"):
                fitted = []
                for item in value:
                    item_value, error = _coerce(spec.items, item, source)
                    if error:
                        return None, f"'{spec.name}': {error}"
                    fitted.append(item_value)
                value = fitted
        elif kind == "object":
            if not isinstance(value, dict):
                return None, f"'{spec.name}' expects an object, got {type(value).__name__}"
            nested = [p for p in spec.properties if p.required and p.name not in value and p.default is None]
            if nested:
                return None, f"'{spec.name}' is missing {', '.join(p.name for p in nested)}"
        elif kind in ("reference", "media"):
            if source not in ("fields", "field_map"):
                return None, f"'{spec.name}' ({kind}) can only come from an explicit value"
    except (TypeError, ValueError) as exc:
        return None, f"'{spec.name}' has an invalid {kind} value ({exc})"
    if isinstance(value, str):
        if spec.max_length and len(value) > spec.max_length:
            return None, f"'{spec.name}' is {len(value)} characters, at most {spec.max_length} allowed"
        if kind == "slug" and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            return None, f"'{spec.name}' is not a valid slug"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if spec.minimum is not None and value < spec.minimum:
            return None, f"'{spec.name}' must be at least {spec.minimum}"
        if spec.maximum is not None and value > spec.maximum:
            return None, f"'{spec.name}' must be at most {spec.maximum}"
    return value, None


def map_publication(capability: ContentTypeCapability, content: Any) -> MappingResult:
    """The payload for *capability* from *content* (PublicationContent)."""
    result = MappingResult()
    standard = _standard_values(content)
    explicit = dict(getattr(content, "fields", None) or {})
    by_name = {spec.name: spec for spec in capability.fields}

    if capability.adapter == "native":   # the classic store keeps its own shape
        result.payload = dict(standard)
        result.mapped = {k: k for k in standard}
        result.unmapped_sources = sorted(f"fields.{k}" for k in explicit)
        return result

    assigned: Dict[str, Tuple[Any, str]] = {}   # destination -> (value, source label)
    consumed: set = set()                       # standard roles already placed
    # 1. Explicit values, by destination name.
    for name, value in explicit.items():
        spec = by_name.get(name)
        if spec is None:
            result.unmapped_sources.append(f"fields.{name}")
        elif spec.read_only:
            result.errors.append(f"'{name}' is read-only on the backend")
        else:
            assigned[name] = (value, f"fields.{name}")
    # 2. The content type's explicit field map (destination -> source).
    for name, source in capability.field_map.items():
        if name in assigned:
            continue
        if source.startswith("fields."):
            if source[len("fields."):] in explicit:
                assigned[name] = (explicit[source[len("fields."):]], source)
        elif source in standard:
            assigned[name] = (standard[source], source)
            consumed.add(source)
    # 3. Semantic equivalents for the standard values still unplaced.
    candidates = _role_candidates(capability.fields)
    for role, value in standard.items():
        if role in consumed:
            continue
        options = [(spec, strong) for spec, strong in candidates.get(role, []) if spec.name not in assigned]
        strong = [spec for spec, is_strong in options if is_strong]
        chosen = strong or [spec for spec, _ in options]
        if len(chosen) > 1:
            result.ambiguous.append(f"'{role}' could fill {sorted(s.name for s in chosen)}: set field_map "
                                    "or send the value in fields")
            consumed.add(role)
        elif chosen:
            assigned[chosen[0].name] = (value, role)
            consumed.add(role)

    for name, (value, source) in assigned.items():
        fitted, error = _coerce(by_name[name], value, "fields" if source.startswith("fields.") else source)
        if error:
            result.errors.append(error)
        else:
            result.payload[name] = fitted
            result.mapped[name] = source
    placed = set(result.mapped.values())
    result.unmapped_sources += [role for role in standard if role not in placed and role not in consumed]
    if "body" in standard and "body" not in placed and not any(a.startswith("'body'") for a in result.ambiguous):
        result.errors.append("the backend has no field for the content body: it would be lost")
    for spec in capability.fields:
        if spec.required and not spec.read_only and spec.name not in result.payload and spec.default is None:
            result.missing_required.append(spec.name)
    result.unmapped_sources = sorted(set(result.unmapped_sources))
    return result
