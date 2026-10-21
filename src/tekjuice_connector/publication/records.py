"""Existing records: reading and improving the backend's own content.

Two S2S actions (contract 1.0, receivers advertising ``content.records``),
for content types whose backend lists, returns and updates records:

``content_inventory`` (read-only) returns one page of a type's records::

    {"contract_version": "1.0", "action": "content_inventory", "website_id": "...",
     "content_type": "product", "page": 1, "limit": 50}
    -> {"status": "ok", "content_type": "product", "page": 1, "more": true,
        "items": [{"resource_id": "12", "slug": "blue-mug", "url": "https://...",
                   "updated_at": "...", "values": {"name": "...", "description": "..."}}]}

``record_update`` changes named fields of one existing record, only if the
record still holds the values it was assessed with (``expected``)::

    {"contract_version": "1.0", "action": "record_update", "website_id": "...",
     "idempotency_key": "...", "content_type": "product", "resource_id": "12", "slug": "blue-mug",
     "changes": {"description": "<p>...</p>"}, "expected": {"description": "<p>Old.</p>"}}
    -> {"status": "applied", "previous": {...}, "current": {...}, "url": "...", "replayed": false}

A record edited since (``record_changed``, 409) is never overwritten; the
previous values make every change reversible (a record_update back to them).
Changes are checked against the type's schema; only writable fields change.
"""

from __future__ import annotations

from typing import Any, Dict

from tekjuice_connector.publication.contract import check_contract_version
from tekjuice_connector.publication.errors import ContractViolationError, MalformedRequestError

ACTION_CONTENT_INVENTORY = "content_inventory"
ACTION_RECORD_UPDATE = "record_update"
CAPABILITY_CONTENT_RECORDS = "content.records"
MAX_LIMIT = 100
MAX_CHANGES = 20
_INVENTORY_FIELDS = frozenset({"contract_version", "action", "website_id", "content_type", "page", "limit"})
_UPDATE_FIELDS = frozenset({"contract_version", "action", "website_id", "idempotency_key", "content_type",
                            "resource_id", "slug", "changes", "expected"})


def _check(data: Dict[str, Any], allowed: frozenset) -> None:
    check_contract_version(data)
    unknown = set(data) - allowed
    if unknown:
        raise MalformedRequestError(f"Unknown request field(s): {', '.join(sorted(unknown))}")
    if not isinstance(data.get("content_type"), str) or not data["content_type"]:
        raise ContractViolationError("'content_type' is required")


def parse_inventory(data: Dict[str, Any]) -> Dict[str, Any]:
    _check(data, _INVENTORY_FIELDS)
    page, limit = data.get("page", 1), data.get("limit", 50)
    if not isinstance(page, int) or page < 1 or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ContractViolationError(f"'page' must be >= 1 and 'limit' between 1 and {MAX_LIMIT}")
    return {"content_type": data["content_type"], "page": page, "limit": limit}


def parse_record_update(data: Dict[str, Any]) -> Dict[str, Any]:
    _check(data, _UPDATE_FIELDS)
    key = data.get("idempotency_key")
    if not isinstance(key, str) or len(key) < 8:
        raise ContractViolationError("'idempotency_key' is required")
    changes, expected = data.get("changes"), data.get("expected", {})
    if not isinstance(changes, dict) or not changes or len(changes) > MAX_CHANGES:
        raise ContractViolationError(f"'changes' must name 1 to {MAX_CHANGES} fields")
    if not isinstance(expected, dict):
        raise ContractViolationError("'expected' must be an object")
    if not (isinstance(data.get("resource_id"), str) or isinstance(data.get("slug"), str)):
        raise ContractViolationError("'resource_id' or 'slug' identifies the record")
    return {"content_type": data["content_type"], "idempotency_key": key, "resource_id": data.get("resource_id"),
            "slug": data.get("slug"), "changes": changes, "expected": expected}
