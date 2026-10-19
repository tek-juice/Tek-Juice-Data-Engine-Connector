"""Connector-owned S2S setup.

The receiver routes are real (FastAPI / Flask / Django test clients); the
Data Engine is a fake transport with the Data Engine's handshake semantics
(tests/fixtures/setup_fixtures.py); only the network is simulated.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import subprocess

import pytest

from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.publication import InMemoryContentStore, PublicationReceiver
from tekjuice_connector.publication.contract import parse_verify_request
from tekjuice_connector.setup import (
    EnvFileSecretStore,
    EnvironmentSecretStore,
    FileSecretStore,
    SetupCode,
    Stage,
    plan_endpoint,
    receiver_secret_fingerprint,
    register_integration,
    validate_endpoint_url,
)
from tekjuice_connector.setup import secret_store as secret_store_module
from tekjuice_connector.setup.endpoint import EndpointError
from tekjuice_connector.setup.selfcheck import verify_request
from tests.fixtures.publication_fixtures import publish_payload
from tests.fixtures.setup_fixtures import (
    API_BASE_URL,
    CONNECTOR_KEY,
    DATA_ENGINE_URL,
    PUBLIC_BASE_URL,
    WEBSITE_ID,
    FakeDataEngine,
    Network,
    fingerprint,
)

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

ROUTE = "/api/integrations/tekjuice/s2s/publish"
ENDPOINT = API_BASE_URL + ROUTE


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.5)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    network = Network()
    engine = FakeDataEngine(network)
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    secrets_store = EnvFileSecretStore(tmp_path / "tekjuice.env", check_git=False)
    receiver = PublicationReceiver(store=store, website_id=WEBSITE_ID, secret_store=secrets_store)
    app = fastapi.FastAPI()
    network.serve_app(API_BASE_URL, TestClient(app))
    clock = Clock()

    class World:
        pass

    w = World()
    w.network, w.engine, w.content, w.receiver, w.app, w.secrets, w.clock = (
        network, engine, store, receiver, app, secrets_store, clock)
    w.connector = TekJuiceConnector(website_id=WEBSITE_ID, connector_key=CONNECTOR_KEY,
                                    data_engine_url=DATA_ENGINE_URL, max_retries=1)

    def setup(**kwargs):
        options = dict(secret_store=w.secrets, receiver=w.receiver, app=w.app, api_base_url=API_BASE_URL,
                       route_path=ROUTE, transport=engine, self_check_post=network.post,
                       sleep=clock.sleep, clock=clock, environ={})
        options.update(kwargs)
        return w.connector.setup(**options)

    w.setup = setup
    return w


def statuses(result):
    return {s.stage: s.status for s in result.stages}


# ── The full setup ───────────────────────────────────────────────────────────

def test_setup_generates_persists_registers_and_verifies(world, monkeypatch):
    calls = []
    import tekjuice_connector.setup.workflow as workflow

    original = workflow.secrets.token_urlsafe
    monkeypatch.setattr(workflow.secrets, "token_urlsafe", lambda n: calls.append(n) or original(n))

    result = world.setup()

    assert result.ready and result.code == SetupCode.INTEGRATION_READY
    assert [s.stage for s in result.stages] == list(Stage)
    assert all(status == "passed" for status in statuses(result).values())
    assert calls.count(48) == 1  # one secret from secrets.token_urlsafe(48), the CSPRNG
    stored = world.secrets.load()
    assert len(stored.active.reveal()) == 64
    assert stored.endpoint_url == ENDPOINT and result.endpoint_url == ENDPOINT
    # Both sides hold the same endpoint and secret.
    assert world.engine.registration["endpoint_url"] == ENDPOINT
    assert world.engine.registration["receiver_secret"] == stored.active.reveal()
    assert result.receiver_secret_fingerprint == fingerprint(stored.active.reveal())
    assert world.engine.registration["auth_scheme"] == "bearer"
    assert world.receiver.receiver_secret == stored.active
    # The route was mounted by the setup.
    assert result.stage(Stage.BACKEND_IDENTIFIED).evidence["route_source"] == "mounted by the setup"


def test_repeated_setup_reuses_the_secret_and_does_not_reverify(world):
    first = world.setup()
    secret = world.secrets.load().active
    verifications = world.engine.verifications
    second = world.setup()
    assert first.ready and second.ready
    assert world.secrets.load().active == secret
    assert first.secret_generated and not second.secret_generated
    assert world.engine.verifications == verifications
    assert second.stage(Stage.BACKEND_IDENTIFIED).evidence["route_source"] == "existing route"
    assert sum(1 for r in world.app.routes if getattr(r, "name", "") == "tekjuice_receive_publication") == 1


def test_no_content_is_published_during_setup(world):
    assert world.setup().ready
    assert world.content.list_published() == [] if hasattr(world.content, "list_published") else True
    assert not any(p.get("action") in ("create", "update") for _, _, p in world.network.requests)


def test_the_canonical_s2s_publication_works_after_setup(world):
    assert world.setup().ready
    status, body = world.engine.publish(publish_payload(website_id=WEBSITE_ID))
    assert status in (200, 201) and body["status"] == "published"
    assert body["published_url"].startswith(PUBLIC_BASE_URL)


# ── Secret persistence and redaction ─────────────────────────────────────────

def test_env_file_is_private_atomic_and_keeps_other_settings(world):
    world.secrets.path.write_text("# app settings\nDATABASE_URL=postgres://x\nTEKJUICE_WEBSITE_ID=w\n")
    assert world.setup().ready
    text = world.secrets.path.read_text()
    assert "DATABASE_URL=postgres://x" in text and "# app settings" in text and "TEKJUICE_WEBSITE_ID=w" in text
    assert text.count("TEKJUICE_RECEIVER_SECRET=") == 1
    assert f"TEKJUICE_PUBLICATION_ENDPOINT_URL={ENDPOINT}" in text
    assert stat.S_IMODE(os.stat(world.secrets.path).st_mode) == 0o600


def test_secret_never_appears_in_results_diagnostics_or_logs(world, caplog):
    caplog.set_level(logging.DEBUG)
    result = world.setup()
    secret = world.secrets.load().active.reveal()
    rendered = [json.dumps(result.to_dict()), repr(result), repr(world.secrets.load()), repr(world.receiver),
                world.connector.diagnostics(include_connectivity=False).to_text(), caplog.text,
                str(world.secrets.load().active)]
    assert all(secret not in text for text in rendered)
    assert CONNECTOR_KEY not in json.dumps(result.to_dict())


def test_git_tracked_env_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    env = tmp_path / ".env"
    env.write_text("A=1\n")
    store = EnvFileSecretStore(env)
    with pytest.raises(secret_store_module.SecretPersistenceError, match="not git-ignored"):
        store.save(secret_store_module.ReceiverSecrets(active=None, endpoint_url=ENDPOINT))
    (tmp_path / ".gitignore").write_text(".env\n")
    store.save(secret_store_module.ReceiverSecrets(active=None, endpoint_url=ENDPOINT))
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", ".env"], check=True)
    with pytest.raises(secret_store_module.SecretPersistenceError, match="tracked by git"):
        store.save(secret_store_module.ReceiverSecrets(active=None, endpoint_url=ENDPOINT))


def test_container_filesystem_is_not_treated_as_persistent(world, monkeypatch):
    monkeypatch.setattr(secret_store_module, "in_container", lambda: True)
    monkeypatch.setattr(secret_store_module, "_mount_for", lambda path: {"mount_point": "/", "fstype": "overlay"})
    result = world.setup()
    assert not result.ready and result.code == SetupCode.SECRET_PERSISTENCE_UNAVAILABLE
    assert "lost when the container is recreated" in result.failed_stage.detail
    assert world.engine.registration is None
    monkeypatch.setattr(secret_store_module, "_mount_for", lambda path: {"mount_point": "/run/secrets",
                                                                         "fstype": "ext4"})
    assert world.setup().ready


def test_read_only_environment_store_reports_instead_of_pretending(world):
    result = world.setup(secret_store=EnvironmentSecretStore(environ={}))
    assert result.code == SetupCode.SECRET_PERSISTENCE_UNAVAILABLE
    assert "secret manager" in result.failed_stage.detail
    assert world.engine.registration is None


def test_existing_injected_secret_is_used_from_a_read_only_store(world):
    secret = "s" * 64
    world.receiver.set_receiver_secrets(secret)
    result = world.setup(secret_store=EnvironmentSecretStore(environ={"TEKJUICE_RECEIVER_SECRET": secret}))
    assert result.ready and not result.secret_generated
    assert world.engine.registration["receiver_secret"] == secret


def test_file_secret_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    store = FileSecretStore(tmp_path / "secrets" / "tekjuice.json")
    store.save(secret_store_module.ReceiverSecrets(active=secret_store_module.SecretStr("a" * 40),
                                                   endpoint_url=ENDPOINT))
    loaded = store.load()
    assert loaded.active.reveal() == "a" * 40 and loaded.endpoint_url == ENDPOINT
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


# ── Rotation and recovery ────────────────────────────────────────────────────

def test_rotation_is_coordinated_and_promotes_only_after_verification(world):
    assert world.setup().ready
    old = world.secrets.load().active
    result = world.setup(rotate=True)
    assert result.ready and result.secret_rotated
    new = world.secrets.load().active
    assert new != old and world.secrets.load().pending is None
    assert world.engine.registration["receiver_secret"] == new.reveal()
    assert world.receiver._matches(new.reveal().encode()) and not world.receiver._matches(old.reveal().encode())


def test_failed_rotation_keeps_both_secrets_and_retry_reuses_the_pending_one(world):
    assert world.setup().ready
    old = world.secrets.load().active
    world.engine.reject_registration = (503, "unavailable")  # Data Engine fails at registration
    failed = world.setup(rotate=True)
    world.engine.reject_registration = None
    assert not failed.ready
    pending = world.secrets.load().pending
    assert pending is not None and world.secrets.load().active == old
    # The receiver keeps accepting the active secret: publication continues.
    assert world.receiver._matches(old.reveal().encode()) and world.receiver._matches(pending.reveal().encode())
    retried = world.setup(rotate=True)
    assert retried.ready and world.secrets.load().active == pending


def test_rollback_rotation_re_registers_the_active_secret(world):
    assert world.setup().ready
    old = world.secrets.load().active
    world.engine.reject_registration = (422, "temporarily rejected")
    assert not world.setup(rotate=True).ready
    world.engine.reject_registration = None
    result = world.setup(rollback_rotation=True)
    assert result.ready and world.secrets.load().pending is None
    assert world.engine.registration["receiver_secret"] == old.reveal()


# ── Endpoint URL ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://api.customer.example/p", "https://localhost/p", "https://10.0.0.5/p", "https://api.customer.example:8443/p",
    "https://user:pw@api.customer.example/p", "https://<actual-customer-api-host>/tekjuice/publish",
    "https://api.customer.example/p#x",
])
def test_invalid_endpoint_urls_are_rejected(url):
    with pytest.raises(EndpointError):
        validate_endpoint_url(url)


def test_endpoint_is_built_from_configured_base_url_and_route():
    assert plan_endpoint(route_path=ROUTE, api_base_url="https://api.customer.example/v2/", environ={}).url \
        == "https://api.customer.example/v2" + ROUTE
    env = plan_endpoint(route_path=ROUTE, environ={"TEKJUICE_API_BASE_URL": API_BASE_URL})
    assert env.url == ENDPOINT and "environment" in env.source
    stored = plan_endpoint(route_path=None, stored_endpoint_url=ENDPOINT, environ={})
    assert stored.url == ENDPOINT and stored.route_path == ROUTE


def test_unknown_api_host_fails_without_using_the_website_domain(world):
    result = world.setup(api_base_url=None)
    assert result.code == SetupCode.API_HOST_NOT_DISCOVERABLE
    assert "may differ from the website's domain" in result.failed_stage.detail
    assert world.engine.registration is None


def test_fastapi_servers_declaration_is_a_base_url_source(world):
    world.app.servers = [{"url": API_BASE_URL}]
    result = world.setup(api_base_url=None)
    assert result.ready
    assert result.stage(Stage.PUBLICATION_ENDPOINT_ESTABLISHED).evidence["source"] == "FastAPI app.servers"


def test_verified_endpoint_is_not_changed_without_confirmation(world):
    assert world.setup().ready
    world.network.serve_app("https://api2.customer.example", TestClient(world.app))
    blocked = world.setup(api_base_url="https://api2.customer.example")
    assert blocked.code == SetupCode.ENDPOINT_CHANGE_UNCONFIRMED
    assert world.engine.registration["endpoint_url"] == ENDPOINT
    moved = world.setup(api_base_url="https://api2.customer.example", allow_endpoint_change=True)
    assert moved.ready and world.engine.registration["endpoint_url"].startswith("https://api2.")


# ── Failures: nothing false, nothing mismatched ──────────────────────────────

def test_invalid_connector_key_stops_before_any_secret_is_generated(world):
    world.engine.connector_key = "another-key"
    result = world.setup()
    assert result.code == SetupCode.ONBOARDING_AUTH_FAILED and not result.ready
    assert world.secrets.load() is None


def test_website_scope_mismatch(world):
    world.engine.website_id = "someone-else"
    assert world.setup().code == SetupCode.ONBOARDING_AUTH_FAILED  # the key is website-scoped (401)


def test_unverified_ownership_is_reported(world):
    world.engine.onboarding_status = "OWNERSHIP_VERIFICATION_PENDING"
    assert world.setup().code == SetupCode.WEBSITE_OWNERSHIP_UNVERIFIED


def test_data_engine_unavailable_changes_nothing(world):
    world.engine.down = True
    result = world.setup()
    assert result.code == SetupCode.DATA_ENGINE_UNREACHABLE and world.secrets.load() is None


def test_backend_unreachable_registers_nothing(world):
    world.network.down = True
    result = world.setup()
    assert result.code == SetupCode.BACKEND_UNREACHABLE
    assert world.engine.registration is None and world.secrets.load().active is not None


def test_receiver_without_the_secret_blocks_registration(world):
    other = PublicationReceiver(store=InMemoryContentStore(PUBLIC_BASE_URL), website_id=WEBSITE_ID,
                                receiver_secret="o" * 64)
    app = fastapi.FastAPI()
    from tekjuice_connector.adapters.fastapi import create_router

    app.include_router(create_router(other, path=ROUTE))
    world.network.serve_app(API_BASE_URL, TestClient(app))
    result = world.setup(receiver=None, app=None)  # CLI-style: the running backend serves the route
    assert result.code == SetupCode.S2S_AUTH_FAILED and result.restart_required
    assert world.engine.registration is None
    assert "nothing was registered" in result.failed_stage.detail


def test_substituted_endpoint_is_detected(world):
    world.network.serve(ENDPOINT, lambda headers, body: (200, {"status": "verified", "challenge": "x",
                                                                "website_id": WEBSITE_ID, "persisted": False}))
    result = world.setup(app=None)
    assert result.code == SetupCode.ENDPOINT_IDENTITY_MISMATCH and world.engine.registration is None


def test_partial_failure_then_retry_reuses_the_same_secret(world):
    world.engine.down = False
    world.engine.reject_registration = (422, "Publication endpoint must use a public DNS hostname")
    failed = world.setup()
    assert failed.code == SetupCode.ENDPOINT_REGISTRATION_FAILED and not failed.ready
    secret = world.secrets.load().active
    world.engine.reject_registration = None
    retried = world.setup()
    assert retried.ready and world.secrets.load().active == secret and not retried.secret_generated


def test_data_engine_verification_failure_is_not_ready(world):
    # The backend answers on the public URL, but a firewall blocks the Data Engine.
    blocked = Network()
    blocked.down = True
    world.engine.network = blocked
    result = world.setup()
    assert not result.ready and result.code == SetupCode.BACKEND_UNREACHABLE
    assert result.stage(Stage.PUBLICATION_CAPABILITY_VERIFIED).status == "failed"


def test_verification_still_running_is_pending_not_ready(world):
    world.engine.verify_pending = False
    original = world.engine._verify
    world.engine._verify = lambda: None  # never completes
    result = world.setup(verification_timeout_seconds=5)
    world.engine._verify = original
    assert not result.ready and result.failed_stage.status == "pending"
    assert result.code == SetupCode.PUBLICATION_CAPABILITY_UNVERIFIED


def test_older_data_engine_without_registration_echo(world):
    world.engine.echo_registration = False
    result = world.setup()
    assert result.ready
    assert result.stage(Stage.ENDPOINT_REGISTERED).evidence["confirmed"] is False


def test_data_engine_holding_another_secret_is_detected(world, monkeypatch):
    original = world.engine._response

    def wrong():
        body = original()
        if "receiver_secret_fingerprint" in body:
            body["receiver_secret_fingerprint"] = "0" * 16
        return body

    world.engine._response = wrong
    assert world.setup().code == SetupCode.ENDPOINT_REGISTRATION_FAILED


# ── Frameworks ───────────────────────────────────────────────────────────────

def test_flask_route_is_registered_and_verified(world):
    flask = pytest.importorskip("flask")
    app = flask.Flask("customer")
    world.network.serve_app(API_BASE_URL, app.test_client())
    result = world.setup(app=app)
    assert result.ready and result.stage(Stage.BACKEND_IDENTIFIED).evidence["framework"] == "flask"
    assert any(r.endpoint.endswith("receive_publication") for r in app.url_map.iter_rules())


def test_flask_after_first_request_reports_the_missing_step(world):
    flask = pytest.importorskip("flask")
    app = flask.Flask("customer")
    app.get("/")(lambda: "ok")
    app.test_client().get("/")
    result = world.setup(app=app)
    assert result.code == SetupCode.PUBLICATION_ROUTE_MISSING and "create_blueprint" in result.failed_stage.detail


def test_django_existing_route_is_found_and_missing_route_is_explained(world):
    pytest.importorskip("django")
    import sys
    import types

    import django
    from django.conf import settings
    from django.urls import clear_url_caches, path

    from tekjuice_connector.adapters.django import create_view

    if not settings.configured:
        settings.configure(DEBUG=False, ALLOWED_HOSTS=["testserver"], SECRET_KEY="setup-test")
        django.setup()
    urls = types.ModuleType("tekjuice_setup_test_urls")
    urls.urlpatterns = []
    sys.modules[urls.__name__] = urls
    previous = getattr(settings, "ROOT_URLCONF", None)
    settings.ROOT_URLCONF = urls.__name__
    clear_url_caches()
    from django.test import Client

    try:
        missing = world.setup(app=settings, route_path=None)
        assert missing.code == SetupCode.PUBLICATION_ROUTE_MISSING
        assert "urls.py" in missing.failed_stage.detail

        urls.urlpatterns = [path("tekjuice/publish", create_view(world.receiver))]
        clear_url_caches()
        world.network.serve_app(API_BASE_URL, Client())
        result = world.setup(app=settings, route_path=None)
        assert result.ready, result.to_dict()
        assert result.stage(Stage.BACKEND_IDENTIFIED).evidence["route"] == "/tekjuice/publish"
        assert result.endpoint_url == API_BASE_URL + "/tekjuice/publish"
    finally:
        settings.ROOT_URLCONF = previous
        clear_url_caches()


def test_unsupported_and_ambiguous_applications(world):
    assert world.setup(app=object()).code == SetupCode.UNSUPPORTED_FRAMEWORK

    class Claims:
        def __init__(self, name):
            self.name = name

        def matches(self, app):
            return isinstance(app, dict)

    from tekjuice_connector.setup import frameworks

    register_integration(Claims("one"))
    register_integration(Claims("two"))
    try:
        assert world.setup(app={}).code == SetupCode.ADAPTER_AMBIGUOUS
    finally:
        del frameworks._INTEGRATIONS[:2]


# ── Contract and compatibility ───────────────────────────────────────────────

def test_self_check_request_is_a_valid_contract_verify_request():
    request = verify_request(WEBSITE_ID, "challenge")
    parsed = parse_verify_request(request)
    assert parsed.website_id == WEBSITE_ID and parsed.publication.action == "create"


def test_credentials_are_kept_separate(world):
    assert world.setup().ready
    secret = world.secrets.load().active.reveal()
    assert secret != CONNECTOR_KEY
    # The connector key never goes to the receiver; the receiver secret goes to
    # the Data Engine only inside the authenticated handshake body.
    assert all(CONNECTOR_KEY not in json.dumps(h) for _, h, _ in world.network.requests)
    assert not world.receiver._matches(CONNECTOR_KEY.encode())


def test_fingerprint_matches_the_data_engine_derivation():
    assert receiver_secret_fingerprint("x" * 64) == fingerprint("x" * 64)


def test_existing_receiver_and_registration_api_still_work():
    from tekjuice_connector import PublicationRegistration

    receiver = PublicationReceiver(store=InMemoryContentStore(PUBLIC_BASE_URL), website_id=WEBSITE_ID,
                                   receiver_secret="r" * 40)
    registration = PublicationRegistration.from_receiver(receiver, endpoint_url=ENDPOINT, framework="fastapi")
    assert registration.to_handshake_dict()["receiver_secret"] == "r" * 40
    with pytest.raises(ValueError):
        PublicationReceiver(store=InMemoryContentStore(PUBLIC_BASE_URL), website_id=WEBSITE_ID)


def test_store_backed_receiver_rejects_until_setup_and_follows_other_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    store = EnvFileSecretStore(tmp_path / "t.env", check_git=False)
    receiver = PublicationReceiver(store=InMemoryContentStore(PUBLIC_BASE_URL), website_id=WEBSITE_ID,
                                   secret_store=store)
    assert not receiver.has_secret
    response = receiver.handle({"Authorization": "Bearer " + "z" * 64}, b"{}")
    assert response.status_code == 401
    # Another process stores a secret: this receiver picks it up on the next miss.
    store.save(secret_store_module.ReceiverSecrets(active=secret_store_module.SecretStr("z" * 64)))
    receiver._loaded_at = -100.0
    body = json.dumps(verify_request(WEBSITE_ID, "c")).encode()
    assert receiver.handle({"Authorization": "Bearer " + "z" * 64}, body).status_code == 200


def test_cli_setup_reports_invalid_configuration_without_secrets(tmp_path, capsys, monkeypatch):
    from tekjuice_connector.cli.main import main

    for key in ("TEKJUICE_WEBSITE_ID", "TEKJUICE_CONNECTOR_KEY", "TEKJUICE_DATA_ENGINE_URL"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / "app.env"
    env.write_text("TEKJUICE_RECEIVER_SECRET=" + "q" * 64 + "\n")
    assert main(["setup", "--env-file", str(env), "--route-path", ROUTE]) == 2
    out = capsys.readouterr()
    assert "q" * 64 not in out.out + out.err


def test_rotation_completed_elsewhere_retires_the_old_secret(tmp_path, monkeypatch):
    """A long-running receiver (the sidecar) follows a rotation another process completed."""
    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    store = EnvFileSecretStore(tmp_path / "t.env", check_git=False)
    old, new = "o" * 64, "n" * 64
    store.save(secret_store_module.ReceiverSecrets(active=secret_store_module.SecretStr(old),
                                                   pending=secret_store_module.SecretStr(new)))
    receiver = PublicationReceiver(store=InMemoryContentStore(PUBLIC_BASE_URL), website_id=WEBSITE_ID,
                                   secret_store=store)
    assert receiver._matches(old.encode()) and receiver._matches(new.encode())
    store.save(secret_store_module.ReceiverSecrets(active=secret_store_module.SecretStr(new)))
    receiver._loaded_at = -100.0  # the reload interval has passed
    body = json.dumps(verify_request(WEBSITE_ID, "c")).encode()
    assert receiver.handle({"Authorization": "Bearer " + old}, body).status_code == 401
    assert receiver.handle({"Authorization": "Bearer " + new}, body).status_code == 200
