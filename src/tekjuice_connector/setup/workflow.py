"""Connector-owned S2S setup.

``ConnectorSetup.run()`` (``TekJuiceConnector.setup()``) does the integration
work end to end and reports evidence for each stage:

1. ``connector_authenticated``: the onboarding handshake
   (``POST /api/v1/connector/handshake`` with ``X-Tek-Juice-Connector-Key``)
   accepts the key for this website;
2. ``backend_identified``: the framework integration, and the receiver
   route found or mounted;
3. ``receiver_secret_configured``: the receiver secret, generated on this
   server with ``secrets.token_urlsafe(48)`` only when none is stored (or a
   rotation was requested), persisted in the secret store, loaded by the
   receiver;
4. ``publication_endpoint_established``: ``TEKJUICE_PUBLICATION_ENDPOINT_URL``
   from the configured API base URL and the receiver's route;
5. ``s2s_auth_verified``: a dry-run ``verify`` request through the public URL
   with that secret is answered by this receiver (nothing is persisted);
6. ``endpoint_registered``: the handshake registers endpoint and secret, and
   the Data Engine confirms the endpoint and the secret's fingerprint;
7. ``publication_capability_verified``: the Data Engine's own dry-run
   verification from its network succeeded;
8. ``ready``: the Data Engine reports the website READY and publication-ready.

A stage runs only when the previous ones passed; nothing is registered with
the Data Engine before the receiver has proved it accepts the secret, so
the two sides never hold different secrets. No content is published.

Rotation (``rotate=True``) stores a pending secret beside the active one;
the receiver accepts both, the Data Engine is re-registered with the
pending one, and only after its verification succeeds does the pending
secret replace the active one. Until then retries reuse the same pending
secret; ``rollback_rotation=True`` re-registers the active secret instead.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from tekjuice_connector.exceptions.api import ApiError
from tekjuice_connector.exceptions.authentication import InvalidCredentialsError, WebsiteIdMismatchError
from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.exceptions.configuration import ConfigurationError
from tekjuice_connector.models.connection import ConnectionResult
from tekjuice_connector.publication.contract import (
    CAPABILITY_CREATE,
    CAPABILITY_VERIFY,
    SUPPORTED_CONTRACT_VERSIONS,
)
from tekjuice_connector.security.secrets import SecretStr
from tekjuice_connector.setup import selfcheck
from tekjuice_connector.setup.endpoint import DEFAULT_ROUTE_PATH, EndpointError, plan_endpoint
from tekjuice_connector.setup.frameworks import RouteRegistrationUnavailable, resolve_integration
from tekjuice_connector.setup.secret_store import ReceiverSecrets, SecretPersistenceError

RECEIVER_FINGERPRINT_PREFIX = b"tekjuice-receiver-secret:v1:"
CONNECTED_STATES = {"CONNECTOR_AUTHENTICATED", "BACKEND_CONNECTED", "READY"}
STALE_FAILURE_GRACE_SECONDS = 30.0


def generate_receiver_secret() -> SecretStr:
    """A new receiver secret from the OS's CSPRNG (``secrets``), 64 URL-safe
    characters (48 random bytes)."""
    return SecretStr(secrets.token_urlsafe(48))


def receiver_secret_fingerprint(secret: "SecretStr | str") -> str:
    """The Data Engine's fingerprint of a receiver secret (same derivation)."""
    raw = secret.reveal() if isinstance(secret, SecretStr) else str(secret)
    return hashlib.sha256(RECEIVER_FINGERPRINT_PREFIX + raw.encode("utf-8")).hexdigest()[:16]


class Stage(str, Enum):
    CONNECTOR_AUTHENTICATED = "connector_authenticated"
    BACKEND_IDENTIFIED = "backend_identified"
    RECEIVER_SECRET_CONFIGURED = "receiver_secret_configured"
    PUBLICATION_ENDPOINT_ESTABLISHED = "publication_endpoint_established"
    S2S_AUTH_VERIFIED = "s2s_auth_verified"
    ENDPOINT_REGISTERED = "endpoint_registered"
    PUBLICATION_CAPABILITY_VERIFIED = "publication_capability_verified"
    READY = "ready"


class SetupCode(str, Enum):
    CONNECTOR_NOT_CONFIGURED = "connector_not_configured"
    ONBOARDING_AUTH_FAILED = "onboarding_auth_failed"
    WEBSITE_OWNERSHIP_UNVERIFIED = "website_ownership_unverified"
    WEBSITE_SCOPE_MISMATCH = "website_scope_mismatch"
    DATA_ENGINE_UNREACHABLE = "data_engine_unreachable"
    UNSUPPORTED_FRAMEWORK = "unsupported_framework"
    ADAPTER_AMBIGUOUS = "adapter_ambiguous"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    API_HOST_NOT_DISCOVERABLE = "api_host_not_discoverable"
    PUBLICATION_ROUTE_MISSING = "publication_route_missing"
    RECEIVER_SECRET_NOT_CONFIGURED = "receiver_secret_not_configured"
    SECRET_PERSISTENCE_UNAVAILABLE = "secret_persistence_unavailable"
    ENDPOINT_CHANGE_UNCONFIRMED = "endpoint_change_unconfirmed"
    ENDPOINT_IDENTITY_MISMATCH = "endpoint_identity_mismatch"
    ENDPOINT_REGISTRATION_FAILED = "endpoint_registration_failed"
    S2S_AUTH_FAILED = "s2s_auth_failed"
    PUBLICATION_CONTRACT_INCOMPATIBLE = "publication_contract_incompatible"
    BACKEND_UNREACHABLE = "backend_unreachable"
    PUBLICATION_CAPABILITY_UNVERIFIED = "publication_capability_unverified"
    INTEGRATION_READY = "integration_ready"


# Data Engine verification failure reasons -> setup codes.
_VERIFICATION_REASONS = {
    "auth_failed": SetupCode.S2S_AUTH_FAILED,
    "unreachable": SetupCode.BACKEND_UNREACHABLE,
    "receiver_error": SetupCode.BACKEND_UNREACHABLE,
    "website_mismatch": SetupCode.WEBSITE_SCOPE_MISMATCH,
    "contract_unsupported": SetupCode.PUBLICATION_CONTRACT_INCOMPATIBLE,
    "capability_missing": SetupCode.PUBLICATION_CONTRACT_INCOMPATIBLE,
    "malformed_response": SetupCode.PUBLICATION_CONTRACT_INCOMPATIBLE,
    "rejected": SetupCode.PUBLICATION_CONTRACT_INCOMPATIBLE,
    "challenge_mismatch": SetupCode.ENDPOINT_IDENTITY_MISMATCH,
    "persisted": SetupCode.PUBLICATION_CONTRACT_INCOMPATIBLE,
}


@dataclass
class StageResult:
    stage: Stage
    status: str  # "passed" | "failed" | "pending" | "skipped"
    detail: str = ""
    code: Optional[SetupCode] = None
    remediation: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage.value, "status": self.status, "detail": self.detail,
                "code": self.code.value if self.code else None, "remediation": self.remediation,
                "evidence": self.evidence}


@dataclass
class SetupResult:
    """What the setup established, stage by stage. Never contains a secret."""

    stages: List[StageResult] = field(default_factory=list)
    ready: bool = False
    code: Optional[SetupCode] = None
    endpoint_url: Optional[str] = None
    receiver_secret_fingerprint: Optional[str] = None
    secret_generated: bool = False
    secret_rotated: bool = False
    restart_required: bool = False
    connection: Optional[ConnectionResult] = None

    def stage(self, stage: Stage) -> Optional[StageResult]:
        return next((s for s in self.stages if s.stage == stage), None)

    @property
    def failed_stage(self) -> Optional[StageResult]:
        return next((s for s in self.stages if s.status in ("failed", "pending")), None)

    def to_dict(self) -> Dict[str, Any]:
        failed = self.failed_stage
        return {
            "ready": self.ready,
            "code": self.code.value if self.code else None,
            "endpoint_url": self.endpoint_url,
            "receiver_secret_fingerprint": self.receiver_secret_fingerprint,
            "secret_generated": self.secret_generated,
            "secret_rotated": self.secret_rotated,
            "restart_required": self.restart_required,
            "remaining_action": failed.remediation if failed else None,
            "stages": [s.to_dict() for s in self.stages],
        }

    def __repr__(self) -> str:
        return f"SetupResult(ready={self.ready}, code={self.code.value if self.code else None!r})"


Handshake = Callable[[Optional[Any]], ConnectionResult]


class ConnectorSetup:
    """Runs the setup; see the module docstring. Construct through
    :meth:`TekJuiceConnector.setup` unless testing."""

    def __init__(
        self,
        *,
        website_id: str,
        handshake: Handshake,
        secret_store: Any,
        receiver: Any = None,
        app: Any = None,
        api_base_url: Optional[str] = None,
        route_path: Optional[str] = None,
        register_route: bool = True,
        public_content_url: Optional[str] = None,
        framework: Optional[str] = None,
        self_check_post: Optional[selfcheck.PostJson] = None,
        receiver_wait_seconds: float = 30.0,
        verification_timeout_seconds: float = 90.0,
        poll_interval_seconds: float = 3.0,
        environ: Optional[dict] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._website_id = str(website_id)
        self._handshake = handshake
        self._store = secret_store
        self._receiver = receiver
        self._app = app
        self._api_base_url = api_base_url
        self._route_path = route_path
        self._register_route = register_route
        self._public_content_url = public_content_url
        self._framework = framework
        self._post = self_check_post
        self._receiver_wait = receiver_wait_seconds
        self._verification_timeout = verification_timeout_seconds
        self._poll = poll_interval_seconds
        self._environ = environ
        self._sleep = sleep
        self._clock = clock

    # ------------------------------------------------------------------

    def run(self, *, rotate: bool = False, rollback_rotation: bool = False,
            allow_endpoint_change: bool = False) -> SetupResult:
        result = SetupResult()

        def passed(stage: Stage, detail: str, **evidence: Any) -> None:
            result.stages.append(StageResult(stage, "passed", detail, evidence=evidence))

        def stop(stage: Stage, code: SetupCode, detail: str, remediation: str, status: str = "failed",
                 **evidence: Any) -> SetupResult:
            result.stages.append(StageResult(stage, status, detail, code, remediation, evidence))
            result.code = code
            return result

        # 1. Connector authentication and website scope ------------------
        try:
            current = self._handshake(None)
        except InvalidCredentialsError:
            return stop(Stage.CONNECTOR_AUTHENTICATED, SetupCode.ONBOARDING_AUTH_FAILED,
                        "The Data Engine rejected the connector key",
                        "Issue a new connector key for this website and set TEKJUICE_CONNECTOR_KEY")
        except WebsiteIdMismatchError:
            return stop(Stage.CONNECTOR_AUTHENTICATED, SetupCode.WEBSITE_SCOPE_MISMATCH,
                        "The connector key belongs to a different website",
                        "Set TEKJUICE_WEBSITE_ID to the website the connector key was issued for")
        except ApiError as exc:
            if exc.status_code is not None and 400 <= exc.status_code < 500:
                return stop(Stage.CONNECTOR_AUTHENTICATED, SetupCode.ONBOARDING_AUTH_FAILED, str(exc),
                            "Fix the onboarding problem the Data Engine reports, then run the setup again")
            return stop(Stage.CONNECTOR_AUTHENTICATED, SetupCode.DATA_ENGINE_UNREACHABLE, str(exc),
                        "The Data Engine is unavailable; run the setup again later (nothing was changed)")
        except TekJuiceConnectorError as exc:
            return stop(Stage.CONNECTOR_AUTHENTICATED, SetupCode.DATA_ENGINE_UNREACHABLE,
                        f"{type(exc).__name__}: {exc}",
                        "Check TEKJUICE_DATA_ENGINE_URL and network access; run the setup again (nothing was changed)")
        result.connection = current
        if current.onboarding_status not in CONNECTED_STATES:
            return stop(Stage.CONNECTOR_AUTHENTICATED, SetupCode.WEBSITE_OWNERSHIP_UNVERIFIED,
                        f"Onboarding status is {current.onboarding_status}",
                        "Verify ownership of the website in Tek Juice first, then run the setup again")
        passed(Stage.CONNECTOR_AUTHENTICATED, f"Connector key accepted for website {current.website_id} "
               f"({current.domain}); onboarding {current.onboarding_status}",
               onboarding_status=current.onboarding_status)

        # Secrets stored so far (also provides a stored endpoint URL).
        try:
            stored = self._store.load() or ReceiverSecrets()
        except SecretPersistenceError as exc:
            return stop(Stage.BACKEND_IDENTIFIED, SetupCode.SECRET_PERSISTENCE_UNAVAILABLE, str(exc),
                        "Repair or replace the secret store file")

        # 2. Backend: framework integration and receiver route -------------
        try:
            integration = resolve_integration(self._app)
        except ValueError as exc:
            return stop(Stage.BACKEND_IDENTIFIED, SetupCode.ADAPTER_AMBIGUOUS, str(exc),
                        "Pass the framework's application object itself as app")
        if self._app is not None and integration is None:
            return stop(Stage.BACKEND_IDENTIFIED, SetupCode.UNSUPPORTED_FRAMEWORK,
                        f"No framework integration for {type(self._app).__name__}",
                        "Mount the receiver with tekjuice_connector.adapters.asgi/wsgi yourself and run the "
                        "setup without app (route_path=...), or register a custom integration")
        route_source = None
        route = None
        if integration is not None:
            try:
                route = integration.find_route(self._app)
            except Exception as exc:  # noqa: BLE001
                return stop(Stage.BACKEND_IDENTIFIED, SetupCode.ADAPTER_UNAVAILABLE,
                            f"{integration.name} integration failed: {type(exc).__name__}",
                            "Install the framework the application uses (optional dependency)")
            if route:
                route_source = "existing route"
                if self._route_path and route.rstrip("/") != self._route_path.rstrip("/"):
                    return stop(Stage.BACKEND_IDENTIFIED, SetupCode.PUBLICATION_ROUTE_MISSING,
                                f"A receiver route is already mounted at {route}, not {self._route_path}",
                                "Use the mounted route (omit route_path) or remove it")
            elif self._register_route:
                if self._receiver is None:
                    return stop(Stage.BACKEND_IDENTIFIED, SetupCode.PUBLICATION_ROUTE_MISSING,
                                "No receiver route is mounted and no receiver was given to mount",
                                "Pass receiver=PublicationReceiver(...) so the setup can mount its route")
                path = self._route_path or DEFAULT_ROUTE_PATH
                try:
                    integration.mount(self._app, self._receiver, path)
                except RouteRegistrationUnavailable as exc:
                    return stop(Stage.BACKEND_IDENTIFIED, SetupCode.PUBLICATION_ROUTE_MISSING, str(exc), str(exc))
                route = integration.find_route(self._app) or path
                route_source = "mounted by the setup"
            else:
                return stop(Stage.BACKEND_IDENTIFIED, SetupCode.PUBLICATION_ROUTE_MISSING,
                            "No receiver route is mounted (register_route=False)",
                            "Mount the receiver route, or allow the setup to register it")
        else:
            route = self._route_path or None
            route_source = "route_path argument (served by the backend; proven by the self-check)" if route else None
            if route is None and not stored.endpoint_url:
                return stop(Stage.BACKEND_IDENTIFIED, SetupCode.PUBLICATION_ROUTE_MISSING,
                            "No application or route path given, and no stored endpoint URL",
                            "Pass app=<your application> (FastAPI, Starlette, Flask, Django settings) or "
                            "route_path='/path/the/receiver/serves'")
        framework = (integration.name if integration else None) or self._framework
        if framework is None:
            from tekjuice_connector.adapters.detect import detect_framework

            framework = detect_framework()
        passed(Stage.BACKEND_IDENTIFIED,
               f"Framework {framework or 'unknown'}; receiver route {route or 'from stored endpoint'}"
               + (f" ({route_source})" if route_source else ""),
               framework=framework, route=route, route_source=route_source)

        # 3. Receiver secret -----------------------------------------------
        rotating = False
        try:
            with self._store.lock():
                stored = self._store.load() or ReceiverSecrets()
                if rollback_rotation:
                    if stored.pending is not None and stored.active is not None:
                        stored = replace(stored, pending=None)
                        self._store.save(stored)
                    candidate, detail = stored.active, "Rotation rolled back: the active secret is registered again"
                elif rotate:
                    if not self._store.writable:
                        raise SecretPersistenceError(
                            "Rotation needs a writable secret store; this one is read-only "
                            f"({self._store.describe().get('store')})")
                    if stored.active is None:
                        stored = replace(stored, active=stored.pending or generate_receiver_secret(), pending=None)
                        result.secret_generated = True
                        candidate, detail = stored.active, "No active secret: a new secret was generated"
                    else:
                        if stored.pending is None:
                            stored = replace(stored, pending=generate_receiver_secret())
                            result.secret_generated = True
                            detail = "Rotation started: a pending secret was generated"
                        else:
                            detail = "Rotation in progress: the stored pending secret is reused"
                        candidate, rotating = stored.pending, True
                    self._store.save(stored)
                elif stored.active is not None:
                    candidate, detail = stored.active, "Existing receiver secret reused (not rotated)"
                elif stored.pending is not None:
                    candidate, rotating = stored.pending, True
                    detail = "Interrupted rotation: the stored pending secret is reused"
                else:
                    if not self._store.writable:
                        raise SecretPersistenceError(
                            "No receiver secret is configured and the secret store is read-only: set "
                            "TEKJUICE_RECEIVER_SECRET in the deployment's secret manager, or use an "
                            "EnvFileSecretStore / FileSecretStore the deployment loads")
                    stored = replace(stored, active=generate_receiver_secret())
                    self._store.save(stored)
                    result.secret_generated = True
                    candidate, detail = stored.active, "A new receiver secret was generated and stored"
        except SecretPersistenceError as exc:
            code = SetupCode.SECRET_PERSISTENCE_UNAVAILABLE
            return stop(Stage.RECEIVER_SECRET_CONFIGURED, code, str(exc), str(exc))
        if candidate is None:
            return stop(Stage.RECEIVER_SECRET_CONFIGURED, SetupCode.RECEIVER_SECRET_NOT_CONFIGURED,
                        "No receiver secret to register", "Run the setup without rollback_rotation")
        if self._receiver is not None:
            self._receiver.set_receiver_secrets(stored.active or candidate, stored.pending)
        result.receiver_secret_fingerprint = receiver_secret_fingerprint(candidate)
        passed(Stage.RECEIVER_SECRET_CONFIGURED, detail, store=self._store.describe(),
               fingerprint=result.receiver_secret_fingerprint, rotation_pending=rotating)

        # 4. Publication endpoint URL ----------------------------------------
        try:
            plan = plan_endpoint(
                route_path=route,
                api_base_url=self._api_base_url,
                framework_base=integration.base_url(self._app) if integration else None,
                stored_endpoint_url=stored.endpoint_url,
                environ=self._environ,
            )
        except EndpointError as exc:
            return stop(Stage.PUBLICATION_ENDPOINT_ESTABLISHED, SetupCode.API_HOST_NOT_DISCOVERABLE, str(exc),
                        "Set TEKJUICE_API_BASE_URL to the backend's public https base URL")
        result.endpoint_url = plan.url
        registered = current.publication_endpoint_url
        if (registered and registered != plan.url and current.publication_status == "verified"
                and not allow_endpoint_change):
            return stop(Stage.PUBLICATION_ENDPOINT_ESTABLISHED, SetupCode.ENDPOINT_CHANGE_UNCONFIRMED,
                        f"The Data Engine publishes to {registered}; the setup would change it to {plan.url}",
                        "Run the setup with allow_endpoint_change=True to move publication to the new endpoint")
        persisted = "not persisted (read-only store; the Data Engine holds it)"
        if self._store.writable and stored.endpoint_url != plan.url:
            try:
                with self._store.lock():
                    latest = self._store.load() or stored
                    self._store.save(replace(latest, endpoint_url=plan.url))
                persisted = "stored as TEKJUICE_PUBLICATION_ENDPOINT_URL"
            except SecretPersistenceError as exc:
                persisted = f"not persisted: {exc}"
        elif stored.endpoint_url == plan.url:
            persisted = "already stored"
        passed(Stage.PUBLICATION_ENDPOINT_ESTABLISHED, f"{plan.url} (base URL from {plan.source}); {persisted}",
               endpoint_url=plan.url, source=plan.source)

        # 5. S2S authentication through the public URL (dry run) ------------
        check = self._self_check(plan.url, candidate)
        if not check.ok:
            code = SetupCode(check.reason) if check.reason in SetupCode._value2member_map_ \
                else SetupCode.BACKEND_UNREACHABLE
            remediation = {
                SetupCode.S2S_AUTH_FAILED: ("Restart the backend so the receiver loads the stored secret "
                                            "(or make sure proxies forward the Authorization header), then "
                                            "run the setup again; the same secret is reused"),
                SetupCode.BACKEND_UNREACHABLE: ("Make sure the backend is running and reachable at the endpoint "
                                                "URL over the public network, then run the setup again"),
                SetupCode.PUBLICATION_ROUTE_MISSING: ("Route the endpoint URL to the receiver (check the API "
                                                      "base URL, proxy and route path)"),
            }.get(code, "Fix the receiver, then run the setup again")
            result.restart_required = code == SetupCode.S2S_AUTH_FAILED and self._receiver is None
            return stop(Stage.S2S_AUTH_VERIFIED, code, check.detail + " (nothing was registered)", remediation,
                        status_code=check.status_code)
        missing = {CAPABILITY_CREATE, CAPABILITY_VERIFY} - set(check.capabilities)
        if missing:
            return stop(Stage.S2S_AUTH_VERIFIED, SetupCode.PUBLICATION_CONTRACT_INCOMPATIBLE,
                        f"The receiver does not support {sorted(missing)}",
                        "Use a content store that supports creating content")
        passed(Stage.S2S_AUTH_VERIFIED, "The public endpoint accepted the secret, served this website and "
               "answered the dry-run verification (nothing persisted)",
               capabilities=check.capabilities, would_publish_url=check.would_publish_url)

        # 6. Registration with the Data Engine -------------------------------
        from tekjuice_connector.publication.registration import PublicationRegistration

        try:
            registration = PublicationRegistration(
                endpoint_url=plan.url,
                receiver_secret=candidate,
                capabilities=tuple(sorted(check.capabilities)),
                contract_versions=tuple(sorted(SUPPORTED_CONTRACT_VERSIONS)),
                framework=framework,
                public_content_url=self._public_content_url,
            )
        except ConfigurationError as exc:
            return stop(Stage.ENDPOINT_REGISTERED, SetupCode.ENDPOINT_REGISTRATION_FAILED, str(exc),
                        "Fix the endpoint configuration")
        try:
            connection = self._handshake(registration)
        except ApiError as exc:
            if exc.status_code is not None and 400 <= exc.status_code < 500:
                return stop(Stage.ENDPOINT_REGISTERED, SetupCode.ENDPOINT_REGISTRATION_FAILED, str(exc),
                            "Fix what the Data Engine reports and run the setup again (the same secret is reused)")
            return stop(Stage.ENDPOINT_REGISTERED, SetupCode.DATA_ENGINE_UNREACHABLE, str(exc),
                        "Run the setup again when the Data Engine is available (the same secret is reused)")
        except TekJuiceConnectorError as exc:
            return stop(Stage.ENDPOINT_REGISTERED, SetupCode.DATA_ENGINE_UNREACHABLE,
                        f"{type(exc).__name__}: {exc}",
                        "Run the setup again when the Data Engine is available (the same secret is reused)")
        result.connection = connection
        if connection.publication_status in (None, "not_configured"):
            return stop(Stage.ENDPOINT_REGISTERED, SetupCode.ENDPOINT_REGISTRATION_FAILED,
                        "The Data Engine did not record the publication connection",
                        "Upgrade the Data Engine to a release that supports S2S publication")
        fingerprint = getattr(connection, "receiver_secret_fingerprint", None)
        endpoint_ok = connection.publication_endpoint_url in (None, plan.url)
        secret_ok = fingerprint in (None, result.receiver_secret_fingerprint)
        if not (endpoint_ok and secret_ok):
            return stop(Stage.ENDPOINT_REGISTERED, SetupCode.ENDPOINT_REGISTRATION_FAILED,
                        "The Data Engine holds a different " + ("endpoint" if not endpoint_ok else "receiver secret")
                        + " than the one registered", "Run the setup again; if it persists, contact Tek Juice")
        confirmed = connection.publication_endpoint_url is not None and fingerprint is not None
        passed(Stage.ENDPOINT_REGISTERED,
               "The Data Engine stored the endpoint and the receiver secret (confirmed by its endpoint URL "
               "and secret fingerprint)" if confirmed else
               "The Data Engine accepted the registration (this Data Engine does not echo the endpoint and "
               "fingerprint; its verification below is the proof)",
               publication_status=connection.publication_status, confirmed=confirmed)

        # 7. The Data Engine's own verification --------------------------------
        connection = self._await_verification(registration, connection)
        result.connection = connection
        if connection.publication_status != "verified" or not connection.publication_ready:
            if connection.publication_status == "failed":
                reason = connection.publication_failure_reason
                code = _VERIFICATION_REASONS.get(reason or "", SetupCode.PUBLICATION_CAPABILITY_UNVERIFIED)
                return stop(Stage.PUBLICATION_CAPABILITY_VERIFIED, code,
                            f"The Data Engine's verification failed ({reason or 'no reason given'})",
                            "Fix the cause, then run the setup again: it re-registers the same secret and the "
                            "Data Engine verifies again" + (
                                "; the rotation stays pending and both secrets keep working" if rotating else ""))
            return stop(Stage.PUBLICATION_CAPABILITY_VERIFIED, SetupCode.PUBLICATION_CAPABILITY_UNVERIFIED,
                        f"The Data Engine has not finished verifying (status {connection.publication_status})",
                        "Run the setup (or status) again shortly", status="pending")
        if rotating:
            with self._store.lock():
                latest = self._store.load() or ReceiverSecrets()
                if latest.pending is not None and latest.pending == candidate:
                    self._store.save(replace(latest, active=candidate, pending=None))
            if self._receiver is not None:
                self._receiver.set_receiver_secrets(candidate)
            result.secret_rotated = True
        passed(Stage.PUBLICATION_CAPABILITY_VERIFIED,
               "The Data Engine verified the receiver from its network with a dry run (nothing published)"
               + ("; the rotated secret is now active" if rotating else ""))

        # 8. Readiness --------------------------------------------------------
        if connection.ready:
            passed(Stage.READY, "The Data Engine reports the website READY for publication")
            result.ready = True
            result.code = SetupCode.INTEGRATION_READY
            return result
        return stop(Stage.READY, SetupCode.PUBLICATION_CAPABILITY_UNVERIFIED,
                    f"Publication is verified but onboarding is {connection.onboarding_status}",
                    f"Data Engine next action: {connection.next_action or 'none reported'}", status="pending")

    # ------------------------------------------------------------------

    def _self_check(self, url: str, secret: SecretStr) -> selfcheck.SelfCheck:
        """Retry while the server is starting or another worker reloads the secret."""
        deadline = self._clock() + self._receiver_wait
        while True:
            check = selfcheck.run_self_check(url, secret.reveal(), self._website_id, post=self._post)
            if check.ok or not check.retryable or self._clock() >= deadline:
                return check
            self._sleep(min(2.0, max(0.0, deadline - self._clock())))

    def _await_verification(self, registration: Any, connection: ConnectionResult) -> ConnectionResult:
        """Repeat the (idempotent) registration until the Data Engine reports
        the verification outcome or the timeout passes."""
        start = self._clock()
        deadline = start + self._verification_timeout
        # An unchanged registration of a connection whose last verification
        # failed answers "failed" while the Data Engine re-verifies it: that
        # answer is final only after a fresh status, or after a grace period.
        stale_failure = connection.publication_status == "failed"
        grace = min(self._verification_timeout, STALE_FAILURE_GRACE_SECONDS)

        def settled(conn: ConnectionResult) -> bool:
            if conn.publication_status == "verified":
                return True
            if conn.publication_status == "failed":
                return not stale_failure or self._clock() - start >= grace
            return False

        while not settled(connection) and self._clock() < deadline:
            self._sleep(self._poll)
            try:
                connection = self._handshake(registration)
            except TekJuiceConnectorError:
                continue
            if connection.publication_status not in ("failed", None):
                stale_failure = False
        return connection
