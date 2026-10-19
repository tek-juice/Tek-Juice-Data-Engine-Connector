"""A fake Data Engine and a fake public network for setup tests.

``FakeDataEngine`` is a connector transport implementing the Data Engine's
handshake semantics (services/onboarding/publication_connection.py):
registration is idempotent; a changed endpoint or secret replaces the stored
one and needs verification; verification posts the dry-run ``verify``
request to the stored endpoint with the stored secret and checks the
challenge, website, ``persisted: false`` and capabilities. Verification is
asynchronous: it runs on the next handshake after registration.

``Network`` routes https URLs to real framework test clients, so the
setup's self-check and the Data Engine's verification reach the actual
mounted receiver routes.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from tekjuice_connector.client.http import HttpResponse
from tekjuice_connector.exceptions.authentication import InvalidCredentialsError
from tekjuice_connector.exceptions.connection import ConnectionError as ConnectorConnectionError

WEBSITE_ID = "website-under-test"
CONNECTOR_KEY = "tjck_test_connector_key_" + "k" * 24
DATA_ENGINE_URL = "https://engine.tekjuice.example"
API_BASE_URL = "https://api.customer.example"
DOMAIN = "customer.example"
PUBLIC_BASE_URL = "https://www.customer.example"

Handler = Callable[[Dict[str, str], bytes], Tuple[int, Any]]


class Network:
    """https URL -> handler(headers, body) -> (status, json body)."""

    def __init__(self) -> None:
        self.routes: Dict[str, Handler] = {}
        self.down = False
        self.requests: List[Tuple[str, Dict[str, str], Dict[str, Any]]] = []

    def serve(self, url: str, handler: Handler) -> None:
        self.routes[url] = handler

    def serve_app(self, base_url: str, client: Any) -> None:
        """Every path under *base_url* goes to a framework test client."""
        self.routes[base_url.rstrip("/") + "/*"] = lambda headers, body, path=None: _client_post(
            client, path, headers, body)

    def post(self, url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float) -> Tuple[int, Any]:
        self.requests.append((url, dict(headers), payload))
        if self.down:
            raise OSError("connection refused")
        body = json.dumps(payload).encode("utf-8")
        if url in self.routes:
            return self.routes[url](headers, body)
        parts = urlsplit(url)
        prefix = f"{parts.scheme}://{parts.netloc}/*"
        if prefix in self.routes:
            return self.routes[prefix](headers, body, path=parts.path)
        raise OSError(f"no route to {url}")


def _client_post(client: Any, path: str, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
    response = client.post(path, data=body, headers=headers) if _is_flask(client) else (
        client.post(path, data=body, content_type="application/json",
                    **{"HTTP_" + k.upper().replace("-", "_"): v for k, v in headers.items()
                       if k.lower() != "content-type"})
        if _is_django(client) else client.post(path, content=body, headers=headers))
    status = getattr(response, "status_code", None)
    try:
        data = response.json() if callable(getattr(response, "json", None)) else json.loads(response.data)
    except Exception:  # noqa: BLE001
        data = None
    return status, data


def _is_flask(client: Any) -> bool:
    return type(client).__module__.startswith("flask")


def _is_django(client: Any) -> bool:
    return type(client).__module__.startswith("django")


def fingerprint(secret: str) -> str:
    return hashlib.sha256(b"tekjuice-receiver-secret:v1:" + secret.encode()).hexdigest()[:16]


class FakeDataEngine:
    """Connector transport with the Data Engine's handshake behaviour."""

    def __init__(self, network: Network, *, onboarding_status: str = "BACKEND_CONNECTED",
                 echo_registration: bool = True) -> None:
        self.network = network
        self.onboarding_status = onboarding_status
        self.echo_registration = echo_registration
        self.connector_key = CONNECTOR_KEY
        self.website_id = WEBSITE_ID
        self.registration: Optional[Dict[str, Any]] = None
        self.status = "not_configured"
        self.failure_reason: Optional[str] = None
        self.verifications = 0
        self.verify_pending = False
        self.down = False
        self.reject_registration: Optional[Tuple[int, str]] = None
        self.payloads: List[Dict[str, Any]] = []

    # Transport protocol (tekjuice_connector.client.http.HttpTransport) ------
    def post(self, path: str, *, connector_key: str, payload: Dict[str, Any]) -> HttpResponse:
        self.payloads.append(payload)
        if self.down:
            raise ConnectorConnectionError("Data Engine unreachable")
        if connector_key != self.connector_key or payload.get("website_id") != self.website_id:
            raise InvalidCredentialsError(status_code=401)
        if self.verify_pending:
            self._verify()
        publication = payload.get("publication")
        if publication is not None:
            if self.reject_registration:
                status, message = self.reject_registration
                return HttpResponse(status_code=status, body={"detail": message})
            if self.onboarding_status not in ("CONNECTOR_AUTHENTICATED", "BACKEND_CONNECTED", "READY"):
                return HttpResponse(status_code=409, body={"detail": "Website ownership must be verified"})
            unchanged = self.registration is not None and all(
                self.registration.get(k) == publication.get(k)
                for k in ("endpoint_url", "receiver_secret", "capabilities", "contract_versions",
                          "framework", "public_content_url"))
            if not unchanged:
                self.registration = dict(publication)
                self.status, self.failure_reason = "pending_verification", None
                self.onboarding_status = "BACKEND_CONNECTED"
            if self.status != "verified":
                self.verify_pending = True
        return HttpResponse(status_code=200, body=self._response())

    # ---------------------------------------------------------------------
    def _verify(self) -> None:
        self.verify_pending = False
        self.verifications += 1
        reg = self.registration
        challenge = secrets.token_urlsafe(16)
        from tekjuice_connector.setup.selfcheck import verify_request

        request = verify_request(self.website_id, challenge)
        headers = {"Authorization": f"Bearer {reg['receiver_secret']}", "Content-Type": "application/json",
                   "X-TekJuice-Contract-Version": "1.0"}
        try:
            status, body = self.network.post(reg["endpoint_url"], headers, request, 15)
        except OSError:
            self.status, self.failure_reason = "failed", "unreachable"
            return
        if status in (401, 403):
            self.status, self.failure_reason = "failed", "auth_failed"
        elif status != 200 or not isinstance(body, dict) or body.get("status") != "verified":
            self.status, self.failure_reason = "failed", "rejected"
        elif body.get("challenge") != challenge:
            self.status, self.failure_reason = "failed", "challenge_mismatch"
        elif body.get("website_id") != self.website_id:
            self.status, self.failure_reason = "failed", "website_mismatch"
        elif body.get("persisted") is not False:
            self.status, self.failure_reason = "failed", "persisted"
        else:
            self.status, self.failure_reason = "verified", None
            self.onboarding_status = "READY"

    def _response(self) -> Dict[str, Any]:
        ready = self.status == "verified"
        body = {
            "website_id": self.website_id, "tenant_id": "tenant-1", "domain": DOMAIN,
            "onboarding_status": self.onboarding_status, "connector_authenticated": True,
            "backend_connected": self.status != "not_configured", "publication_status": self.status,
            "publication_ready": ready, "ready": ready and self.onboarding_status == "READY",
            "next_action": None if ready else "await_publication_verification",
        }
        if self.echo_registration and self.registration:
            body.update({
                "publication_endpoint_url": self.registration["endpoint_url"],
                "receiver_secret_fingerprint": fingerprint(self.registration["receiver_secret"]),
                "publication_failure_reason": self.failure_reason,
            })
        return body

    # Publishing the way the Data Engine's execution jobs do (S2S) ---------
    def publish(self, payload: Dict[str, Any]) -> Tuple[int, Any]:
        reg = self.registration
        headers = {"Authorization": f"Bearer {reg['receiver_secret']}", "Content-Type": "application/json",
                   "X-TekJuice-Contract-Version": "1.0", "Idempotency-Key": payload["idempotency_key"]}
        return self.network.post(reg["endpoint_url"], headers, payload, 30)
