"""Non-mutating proof that the public endpoint reaches this receiver.

The setup sends the same dry-run ``verify`` request the Data Engine sends
(contract 1.0, ``dry_run: true``, a random challenge) to the effective
endpoint URL, through the public network path, with the receiver secret it
is about to register. Passing proves, before anything is registered:

* the URL routes to a receiver (not another service or a 404);
* that receiver accepts this secret (S2S authentication is aligned);
* it serves this website (website scope) and echoes the challenge (it is
  this receiver, not a substituted endpoint);
* it supports the contract and persisted nothing.

Redirects are not followed: a redirect would send the bearer secret to
another URL.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from tekjuice_connector.publication.contract import CONTRACT_VERSION, PublicationContent

PostJson = Callable[[str, Dict[str, str], Dict[str, Any], float], Tuple[int, Any]]

REASON_UNREACHABLE = "backend_unreachable"
REASON_ROUTE_MISSING = "publication_route_missing"
REASON_AUTH_FAILED = "s2s_auth_failed"
REASON_CONTRACT = "publication_contract_incompatible"
REASON_IDENTITY = "endpoint_identity_mismatch"


@dataclass
class SelfCheck:
    ok: bool
    reason: Optional[str] = None
    detail: str = ""
    status_code: Optional[int] = None
    capabilities: list = field(default_factory=list)
    would_publish_url: Optional[str] = None
    # Worth retrying: the server may be starting, another worker may still
    # be loading the secret, or the backend's storage is briefly unavailable.
    retryable: bool = False


def verify_request(website_id: str, challenge: str) -> Dict[str, Any]:
    nonce = secrets.token_hex(6)
    content = {
        "title": "Tek Juice connector setup check",
        "body": "<p>Dry-run verification request. This content must not be persisted.</p>",
        "slug": f"tekjuice-verification-{nonce}",
    }
    digest = PublicationContent(title=content["title"], body=content["body"], slug=content["slug"],
                                excerpt=None, meta_description=None, meta_keywords=[]).content_hash()
    execution_id = f"tekjuice-verification-{nonce}"
    return {
        "contract_version": CONTRACT_VERSION,
        "action": "verify",
        "website_id": website_id,
        "challenge": challenge,
        "dry_run": True,
        "publication": {
            "contract_version": CONTRACT_VERSION,
            "execution_id": execution_id,
            "website_id": website_id,
            "action": "create",
            "idempotency_key": f"tekjuice:{website_id}:{execution_id}:create:{digest}",
            "content": content,
            "metadata": {"content_hash": digest},
        },
    }


def _requests_post(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float) -> Tuple[int, Any]:
    import requests

    response = requests.post(url, json=payload, headers=headers, timeout=(5.0, timeout), allow_redirects=False)
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body


def run_self_check(endpoint_url: str, secret: str, website_id: str, *,
                   post: Optional[PostJson] = None, timeout: float = 15.0) -> SelfCheck:
    challenge = secrets.token_urlsafe(24)
    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-TekJuice-Contract-Version": CONTRACT_VERSION,
    }
    try:
        status, body = (post or _requests_post)(endpoint_url, headers, verify_request(website_id, challenge), timeout)
    except Exception as exc:  # noqa: BLE001 - every transport failure means "not reachable"
        # Never include the request (it carries the secret) in the detail.
        return SelfCheck(False, REASON_UNREACHABLE, f"{type(exc).__name__} reaching {endpoint_url}",
                         retryable=True)

    if status in (401, 403):
        return SelfCheck(False, REASON_AUTH_FAILED,
                         f"The endpoint rejected the receiver secret (HTTP {status}): the running receiver does "
                         "not hold it yet (restart it to load the stored secret) or a proxy strips the "
                         "Authorization header", status, retryable=True)
    if 300 <= status < 400:
        return SelfCheck(False, REASON_ROUTE_MISSING,
                         f"The endpoint redirects (HTTP {status}); register the final https URL", status)
    if status in (404, 405):
        return SelfCheck(False, REASON_ROUTE_MISSING,
                         f"No publication receiver answers at {endpoint_url} (HTTP {status})", status)
    if status == 422 or status == 400:
        return SelfCheck(False, REASON_CONTRACT, f"The receiver rejected the contract request (HTTP {status})", status)
    if status >= 500 or status != 200:
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        return SelfCheck(False, REASON_UNREACHABLE,
                         f"The receiver failed (HTTP {status}" + (f", {code}: {message}" if code else "") + ")",
                         status, retryable=code in (None, "storage_unavailable"))
    if not isinstance(body, dict):
        return SelfCheck(False, REASON_CONTRACT, "The endpoint did not answer with a contract JSON object", status)
    if body.get("status") != "verified" or not secrets.compare_digest(str(body.get("challenge") or ""), challenge):
        return SelfCheck(False, REASON_IDENTITY, "The endpoint did not echo the challenge: it is not this receiver",
                         status)
    if str(body.get("website_id") or "") != str(website_id):
        return SelfCheck(False, REASON_IDENTITY,
                         f"The endpoint serves website {body.get('website_id')!r}, not {website_id!r}", status)
    if body.get("persisted") is not False:
        return SelfCheck(False, REASON_CONTRACT, "The receiver did not confirm the dry run persisted nothing", status)
    return SelfCheck(True, None, "verified", status, sorted(body.get("capabilities") or []),
                     body.get("would_publish_url"))
