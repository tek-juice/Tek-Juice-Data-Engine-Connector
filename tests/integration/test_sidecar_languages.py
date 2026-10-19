"""The sidecar with content APIs written in other languages.

Real processes: the reference content API (Node.js; PHP and Go when their
runtime or a local Docker image is available), the sidecar's HTTP server,
real HTTP between them. The Data Engine is the fake transport with the Data
Engine's handshake/verification semantics (tests/fixtures/setup_fixtures.py);
its verification and the setup's self-check reach the sidecar over real HTTP
(the public hostname is mapped to localhost).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
import requests

from tekjuice_connector.setup import SetupCode, Stage
from tekjuice_connector.setup import secret_store as secret_store_module
from tekjuice_connector.sidecar import Sidecar, SidecarConfig
from tests.fixtures.publication_fixtures import publish_payload
from tests.fixtures.setup_fixtures import (
    API_BASE_URL,
    CONNECTOR_KEY,
    DATA_ENGINE_URL,
    WEBSITE_ID,
    FakeDataEngine,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "sidecar"
TOKEN = "content-api-token-" + "c" * 32
TEMPLATE = "https://www.customer.example/blog/{slug}"
ROUTE = "/tekjuice/publish"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _docker_image(name: str) -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", name], capture_output=True).returncode == 0


def _backend_command(language: str, port: int, data: Path):
    env = {**os.environ, "TEKJUICE_CONTENT_API_TOKEN": TOKEN, "PUBLIC_URL_TEMPLATE": TEMPLATE,
           "PORT": str(port), "LISTEN": f"127.0.0.1:{port}", "CONTENT_FILE": str(data / "content.json")}
    source = EXAMPLES / language
    if language == "node" and shutil.which("node"):
        return ["node", str(source / "content-api.js")], env
    if language == "php":
        if shutil.which("php"):
            return ["php", "-S", f"127.0.0.1:{port}", str(source / "content-api.php")], env
        if _docker_image("php:8.3-cli"):
            return ["docker", "run", "--rm", "--init", "--network", "host", "-e", f"TEKJUICE_CONTENT_API_TOKEN={TOKEN}",
                    "-e", f"PUBLIC_URL_TEMPLATE={TEMPLATE}", "-e", "CONTENT_FILE=/tmp/content.json",
                    "-v", f"{source}:/app:ro", "php:8.3-cli", "php", "-S", f"127.0.0.1:{port}",
                    "/app/content-api.php"], env
    if language == "go":
        if shutil.which("go"):
            return ["go", "run", str(source / "main.go")], env
        if _docker_image("golang:1.22-alpine"):
            return ["docker", "run", "--rm", "--init", "--network", "host", "-e", f"TEKJUICE_CONTENT_API_TOKEN={TOKEN}",
                    "-e", f"PUBLIC_URL_TEMPLATE={TEMPLATE}", "-e", f"LISTEN=127.0.0.1:{port}",
                    "-v", f"{source}:/app:ro", "-w", "/app", "golang:1.22-alpine", "go", "run", "main.go"], env
    if language in FRAMEWORKS and _docker_image(f"tekjuice-example-{language}:test"):
        return ["docker", "run", "--rm", "--init", "--network", "host", "-e", f"PORT={port}", "-e", "HOST=127.0.0.1",
                "-e", f"TEKJUICE_CONTENT_API_TOKEN={TOKEN}", "-e", f"PUBLIC_URL_TEMPLATE={TEMPLATE}",
                f"tekjuice-example-{language}:test"], env
    return None, env


# Framework reference images (examples/sidecar/frameworks/build-images.sh).
FRAMEWORKS = ("express", "laravel", "rails", "spring-boot", "aspnet")


def _wait_for(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            requests.get(url, timeout=1)
            return
        except requests.RequestException:
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not start")


class PublicNetwork:
    """Maps the public API host to the sidecar on localhost (real HTTP)."""

    def __init__(self, sidecar_port: int) -> None:
        self.port = sidecar_port

    def post(self, url, headers, payload, timeout):
        assert url.startswith(API_BASE_URL)
        response = requests.post(f"http://127.0.0.1:{self.port}{url[len(API_BASE_URL):]}", json=payload,
                                 headers=headers, timeout=timeout, allow_redirects=False)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None


@pytest.fixture(params=["node", "php", "go", *FRAMEWORKS])
def stack(request, tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    backend_port = _free_port()
    command, env = _backend_command(request.param, backend_port, tmp_path)
    if command is None:
        pytest.skip(f"no {request.param} runtime or local Docker image "
                    "(frameworks: examples/sidecar/frameworks/build-images.sh)")
    backend = subprocess.Popen(command, env=env, cwd=tmp_path, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        _wait_for(f"http://127.0.0.1:{backend_port}/tekjuice/capabilities", timeout=120)
        config = SidecarConfig.from_env({
            "TEKJUICE_WEBSITE_ID": WEBSITE_ID, "TEKJUICE_CONNECTOR_KEY": CONNECTOR_KEY,
            "TEKJUICE_DATA_ENGINE_URL": DATA_ENGINE_URL, "TEKJUICE_API_BASE_URL": API_BASE_URL,
            "TEKJUICE_CONTENT_API_URL": f"http://127.0.0.1:{backend_port}/tekjuice",
            "TEKJUICE_CONTENT_API_TOKEN": TOKEN, "TEKJUICE_ROUTE_PATH": ROUTE,
            "TEKJUICE_DATA_DIR": str(tmp_path / "sidecar"), "TEKJUICE_LISTEN": "127.0.0.1:0",
        })
        sidecar = Sidecar(config)
        sidecar.start(run_setup=False)
        network = PublicNetwork(sidecar.port)
        engine = FakeDataEngine(network)
        sidecar._setup_options = dict(transport=engine, self_check_post=network.post,
                                      poll_interval_seconds=0.05, verification_timeout_seconds=5)
        yield type("Stack", (), dict(language=request.param, sidecar=sidecar, engine=engine, network=network,
                                     backend_port=backend_port, data=tmp_path))
        sidecar.stop()
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()
            backend.wait(timeout=10)


def _backend_get(stack, slug):
    response = requests.get(f"http://127.0.0.1:{stack.backend_port}/tekjuice/content/{slug}",
                            headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5)
    return response.status_code, (response.json() if response.content else None)


def test_sidecar_sets_up_and_publishes_into_a_non_python_backend(stack):
    status, body = requests.get(f"http://127.0.0.1:{stack.sidecar.port}/readyz", timeout=5).status_code, None
    assert status == 503  # not set up yet

    result = stack.sidecar.run_setup_once()
    assert result.ready, result.to_dict()
    assert result.endpoint_url == API_BASE_URL + ROUTE
    assert stack.engine.registration["endpoint_url"] == API_BASE_URL + ROUTE
    secret = stack.sidecar.secret_store.load().active.reveal()
    assert stack.engine.registration["receiver_secret"] == secret and len(secret) == 64
    ready = requests.get(f"http://127.0.0.1:{stack.sidecar.port}/readyz", timeout=5)
    assert ready.status_code == 200 and secret not in ready.text
    # Setup published nothing into the backend.
    assert not (stack.data / "content.json").exists() or json.loads((stack.data / "content.json").read_text()) == {}

    # The Data Engine publishes over S2S; the content lands in the backend.
    payload = publish_payload(website_id=WEBSITE_ID, slug="sidecar-guide", execution_id="exec-1")
    status, body = stack.engine.publish(payload)
    assert status in (200, 201), body
    assert body["published_url"] == TEMPLATE.replace("{slug}", "sidecar-guide")
    backend_status, record = _backend_get(stack, "sidecar-guide")
    assert backend_status == 200 and record["idempotency_key"] == payload["idempotency_key"]

    # Redelivery is idempotent (the sidecar's ledger), then an update.
    again_status, again = stack.engine.publish(payload)
    assert again_status in (200, 201) and again["published_url"] == body["published_url"]
    update = publish_payload(website_id=WEBSITE_ID, slug="sidecar-guide", execution_id="exec-2",
                             action="update", title="Sidecar Guide, revised")
    update_status, updated = stack.engine.publish(update)
    assert update_status == 200, updated


def test_backend_rejections_and_conflicts_reach_the_data_engine(stack):
    assert stack.sidecar.run_setup_once().ready
    requests.post(f"http://127.0.0.1:{stack.backend_port}/tekjuice/content",
                  headers={"Authorization": f"Bearer {TOKEN}"},
                  json={"slug": "taken", "title": "Customer page", "body": "<p>x</p>"}, timeout=5)
    status, body = stack.engine.publish(publish_payload(website_id=WEBSITE_ID, slug="taken", execution_id="e3"))
    assert status == 409 and body["error"]["code"] == "duplicate_slug"


def test_wrong_content_api_token_blocks_readiness(stack):
    stack.sidecar.content_store._token = "wrong-" + "x" * 30
    result = stack.sidecar.run_setup_once()
    assert not result.ready
    assert result.code in (SetupCode.BACKEND_UNREACHABLE, SetupCode.PUBLICATION_CONTRACT_INCOMPATIBLE)
    assert result.failed_stage.stage == Stage.S2S_AUTH_VERIFIED
    assert stack.engine.registration is None


def test_rotation_from_another_process_reaches_the_running_sidecar(stack):
    if stack.language != "node":
        pytest.skip("rotation is independent of the backend language")
    from tekjuice_connector import TekJuiceConnector
    from tekjuice_connector.setup import FileSecretStore

    assert stack.sidecar.run_setup_once().ready
    old = stack.sidecar.secret_store.load().active
    # What `docker exec tekjuice-connector tekjuice setup --secret-file ... --rotate` does.
    cli = TekJuiceConnector(website_id=WEBSITE_ID, connector_key=CONNECTOR_KEY, data_engine_url=DATA_ENGINE_URL)
    result = cli.setup(secret_store=FileSecretStore(stack.sidecar.config.secret_file), route_path=ROUTE,
                       api_base_url=API_BASE_URL, rotate=True, transport=stack.engine,
                       self_check_post=stack.network.post, poll_interval_seconds=0.05,
                       verification_timeout_seconds=5, receiver_wait_seconds=15)
    assert result.ready and result.secret_rotated, result.to_dict()
    new = stack.sidecar.secret_store.load().active
    assert new != old and stack.engine.registration["receiver_secret"] == new.reveal()
    # The running sidecar publishes with the new secret and has retired the old one.
    stack.sidecar.receiver._loaded_at = -100.0
    status, _ = stack.engine.publish(publish_payload(website_id=WEBSITE_ID, slug="after-rotation", execution_id="r1"))
    assert status in (200, 201)
    stack.sidecar.receiver._loaded_at = -100.0
    assert not stack.sidecar.receiver._matches(old.reveal().encode())
