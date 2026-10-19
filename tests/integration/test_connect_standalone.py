"""Standalone endpoint (what `tekjuice connect` installs): no backend code at all.

Real HTTP: the sidecar's server with its own article store, the fake Data
Engine (handshake and verification semantics), a local "website" serving the
HTML shell. The backend can be written in anything: nothing of it is used.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from tekjuice_connector.setup import secret_store as secret_store_module
from tekjuice_connector.sidecar import Sidecar, SidecarConfig
from tests.fixtures.publication_fixtures import publish_payload
from tests.fixtures.setup_fixtures import API_BASE_URL, CONNECTOR_KEY, DATA_ENGINE_URL, WEBSITE_ID, FakeDataEngine

ARTICLES = "https://www.customer.example/articles"
SHELL = "<!doctype html><html><head><title>Customer</title></head><body><div id='root'></div></body></html>"


class PublicNetwork:
    def __init__(self, port: int) -> None:
        self.port = port

    def post(self, url, headers, payload, timeout):
        assert url.startswith(API_BASE_URL)
        response = requests.post(f"http://127.0.0.1:{self.port}{url[len(API_BASE_URL):]}", json=payload,
                                 headers=headers, timeout=timeout)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None


@pytest.fixture
def site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = SHELL.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture
def standalone(tmp_path, monkeypatch, site):
    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    config = SidecarConfig.from_env({
        "TEKJUICE_WEBSITE_ID": WEBSITE_ID, "TEKJUICE_CONNECTOR_KEY": CONNECTOR_KEY,
        "TEKJUICE_DATA_ENGINE_URL": DATA_ENGINE_URL, "TEKJUICE_API_BASE_URL": API_BASE_URL,
        "TEKJUICE_ARTICLES_URL": ARTICLES, "TEKJUICE_SHELL_URL": site,
        "TEKJUICE_DATA_DIR": str(tmp_path / "tekjuice"), "TEKJUICE_LISTEN": "127.0.0.1:0",
    })
    sidecar = Sidecar(config)
    sidecar.start(run_setup=False)
    network = PublicNetwork(sidecar.port)
    engine = FakeDataEngine(network)
    sidecar._setup_options = dict(transport=engine, self_check_post=network.post, poll_interval_seconds=0.05,
                                  verification_timeout_seconds=5)
    yield sidecar, engine
    sidecar.stop()


def test_connects_and_publishes_without_any_backend_code(standalone):
    sidecar, engine = standalone
    assert sidecar.config.standalone
    result = sidecar.run_setup_once()
    assert result.ready, result.to_dict()
    assert engine.registration["endpoint_url"] == API_BASE_URL + "/tekjuice/publish"
    # Only what is really served is advertised: publication, not the site's sitemap or page changes.
    capabilities = set(engine.registration["capabilities"])
    assert {"publish.create", "publish.update"} <= capabilities
    assert not capabilities & {"seo.sitemap", "seo.prerender", "site.changes", "ai.llms_txt"}

    status, body = engine.publish(publish_payload(website_id=WEBSITE_ID, slug="moving-costs", execution_id="e1",
                                                  title="Moving costs in 2026"))
    assert status in (200, 201), body
    assert body["published_url"] == ARTICLES + "/moving-costs"

    base = f"http://127.0.0.1:{sidecar.port}"
    page = requests.get(base + "/articles/moving-costs", timeout=5)
    assert page.status_code == 200 and "Moving costs in 2026" in page.text
    assert "<title>" in page.text and "id=\"root\"" in page.text.replace("'", '"')
    assert page.headers["Link"] == f'<{ARTICLES}/moving-costs>; rel="canonical"'
    index = requests.get(base + "/articles", timeout=5)
    assert index.status_code == 200 and "moving-costs" in index.text
    assert requests.get(base + "/articles/missing-page", timeout=5).status_code == 404
    assert requests.get(base + "/somewhere-else", timeout=5).status_code == 404

    # Redelivery is idempotent; an update replaces the article.
    again_status, again = engine.publish(publish_payload(website_id=WEBSITE_ID, slug="moving-costs",
                                                         execution_id="e1", title="Moving costs in 2026"))
    assert again_status in (200, 201) and again["published_url"] == body["published_url"]
    update_status, _ = engine.publish(publish_payload(website_id=WEBSITE_ID, slug="moving-costs", execution_id="e2",
                                                      action="update", title="Moving costs, revised"))
    assert update_status == 200
    assert "Moving costs, revised" in requests.get(base + "/articles/moving-costs", timeout=5).text


def test_articles_survive_a_restart(standalone, tmp_path):
    sidecar, engine = standalone
    assert sidecar.run_setup_once().ready
    engine.publish(publish_payload(website_id=WEBSITE_ID, slug="kept-article", execution_id="e9"))
    restarted = Sidecar(sidecar.config)
    assert restarted.content_store.find_by_slug("kept-article") is not None
    assert restarted.secret_store.load().active.reveal() == sidecar.secret_store.load().active.reveal()


@pytest.mark.parametrize("value", ["http://www.customer.example/articles", "https://www.customer.example/",
                                   "https://www.customer.example/articles?x=1"])
def test_articles_url_must_be_an_https_path(value):
    with pytest.raises(ValueError):
        SidecarConfig.from_env({"TEKJUICE_WEBSITE_ID": "w", "TEKJUICE_CONNECTOR_KEY": "k",
                                "TEKJUICE_DATA_ENGINE_URL": "https://e.example", "TEKJUICE_ARTICLES_URL": value})


def test_neither_content_api_nor_articles_url_is_a_configuration_error():
    with pytest.raises(ValueError, match="TEKJUICE_ARTICLES_URL"):
        SidecarConfig.from_env({"TEKJUICE_WEBSITE_ID": "w", "TEKJUICE_CONNECTOR_KEY": "k",
                                "TEKJUICE_DATA_ENGINE_URL": "https://e.example"})
