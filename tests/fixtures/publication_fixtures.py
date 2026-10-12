"""Publication contract fixtures mirroring the Data Engine's v1.0 payloads.

Placeholder values only — no real secrets.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

WEBSITE_ID = "website-under-test"
RECEIVER_SECRET = "test-receiver-secret-" + "s" * 32
PUBLIC_BASE_URL = "https://www.customer.example"
AUTH_HEADERS = {
    "Authorization": f"Bearer {RECEIVER_SECRET}",
    "Content-Type": "application/json",
    "X-TekJuice-Contract-Version": "1.0",
}


def content_hash(content: Dict[str, Any]) -> str:
    canonical = "\n".join(
        [
            str(content.get("title") or ""),
            str(content.get("body") or ""),
            str(content.get("slug") or ""),
            str(content.get("excerpt") or ""),
            str(content.get("meta_description") or ""),
            ",".join(content.get("meta_keywords") or []),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def publish_payload(
    *,
    action: str = "create",
    slug: str = "moving-cost-guide",
    execution_id: str = "execution-1",
    title: str = "Moving Cost Guide",
    body: str = "<h1>Moving Cost Guide</h1><p>Plan every cost.</p>",
    idempotency_key: Optional[str] = None,
    website_id: str = WEBSITE_ID,
) -> Dict[str, Any]:
    content = {
        "title": title,
        "body": body,
        "slug": slug,
        "excerpt": "Plan every cost.",
        "meta_description": "A guide to moving costs.",
        "meta_keywords": ["moving costs"],
    }
    digest = content_hash(content)
    return {
        "contract_version": "1.0",
        "execution_id": execution_id,
        "website_id": website_id,
        "action": action,
        "idempotency_key": idempotency_key
        or f"tekjuice:{website_id}:{execution_id}:{action}:{digest}",
        "content": content,
        "metadata": {
            "content_strategy_id": "strategy-1",
            "opportunity_id": None,
            "content_hash": digest,
        },
    }


def verify_payload(*, challenge: str = "challenge-abc", **publication: Any) -> Dict[str, Any]:
    return {
        "contract_version": "1.0",
        "action": "verify",
        "website_id": publication.get("website_id", WEBSITE_ID),
        "challenge": challenge,
        "dry_run": True,
        "publication": publish_payload(
            slug=publication.pop("slug", "tekjuice-verification-0a1b2c"),
            execution_id="tekjuice-verification-0a1b2c",
            **publication,
        ),
    }


def encode(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")
