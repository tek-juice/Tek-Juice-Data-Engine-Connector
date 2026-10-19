"""Sidecar rules: content API client, configuration, readiness."""

from __future__ import annotations

import json

import pytest

from tekjuice_connector.publication.contract import PublicationContent
from tekjuice_connector.publication.errors import (
    ContentStoreError,
    ContractViolationError,
    DuplicateSlugError,
    TargetNotFoundError,
)
from tekjuice_connector.sidecar import HttpContentStore, SidecarConfig, is_private_url
from tekjuice_connector.sidecar.content_api import ContentApiConfigurationError

TEMPLATE = "https://www.example.com/blog/{slug}"
CONTENT = PublicationContent(title="Guide", body="<p>Guide</p>", slug="guide")


class FakeApi:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        path = url.split("/tekjuice", 1)[1]
        handler = self.responses.get((method, path))
        if isinstance(handler, Exception):
            raise handler
        return handler if handler else (404, None)


def store(tmp_path, api, **kwargs):
    return HttpContentStore("http://backend:3000/tekjuice", ledger_path=str(tmp_path / "l.db"), token="t" * 40,
                            public_url_template=kwargs.pop("template", TEMPLATE), request=api, **kwargs)


def test_create_records_the_ledger_and_redelivery_is_recognised(tmp_path):
    api = FakeApi({("POST", "/content"): (201, {"slug": "guide", "url": "https://www.example.com/g", "id": "7"})})
    s = store(tmp_path, api)
    stored = s.create(CONTENT, execution_id="e1", idempotency_key="k1")
    assert stored.published_url == "https://www.example.com/g" and stored.resource_id == "7"
    assert s.find_by_idempotency_key("k1") == stored and s.find_by_slug("guide") == stored
    method, _, headers, body = api.calls[-1]
    assert headers["Authorization"] == "Bearer " + "t" * 40 and headers["Idempotency-Key"] == "k1"
    assert body["idempotency_key"] == "k1" and body["content_hash"] == CONTENT.content_hash()


def test_interrupted_create_is_recovered_from_the_backend(tmp_path):
    api = FakeApi({("POST", "/content"): (409, None),
                   ("GET", "/content/guide"): (200, {"slug": "guide", "url": "https://x.example/g",
                                                     "idempotency_key": "k1"})})
    assert store(tmp_path, api).create(CONTENT, execution_id="e1", idempotency_key="k1").slug == "guide"
    api.responses[("GET", "/content/guide")] = (200, {"slug": "guide", "idempotency_key": "someone-else"})
    with pytest.raises(DuplicateSlugError):
        store(tmp_path, api).create(CONTENT, execution_id="e2", idempotency_key="k2")


@pytest.mark.parametrize("response,error", [
    ((422, {"errors": ["title too long"]}), ContractViolationError),
    ((503, None), ContentStoreError),
    ((401, None), ContentApiConfigurationError),
    (OSError("refused"), ContentStoreError),
])
def test_backend_responses_map_to_contract_errors(tmp_path, response, error):
    api = FakeApi({("POST", "/content"): response})
    with pytest.raises(error):
        store(tmp_path, api).create(CONTENT, execution_id="e", idempotency_key="k")


def test_update_and_missing_target(tmp_path):
    api = FakeApi({("POST", "/content"): (201, {"slug": "guide", "id": "1"}),
                   ("PUT", "/content/guide"): (200, {"slug": "guide", "id": "1"})})
    s = store(tmp_path, api)
    existing = s.create(CONTENT, execution_id="e1", idempotency_key="k1")
    assert s.update(existing, CONTENT, execution_id="e2", idempotency_key="k2").idempotency_key == "k2"
    api.responses[("PUT", "/content/guide")] = (404, None)
    with pytest.raises(TargetNotFoundError):
        s.update(existing, CONTENT, execution_id="e3", idempotency_key="k3")


def test_capabilities_decide_update_validate_and_public_url(tmp_path):
    api = FakeApi({("GET", "/capabilities"): (200, {"update": False, "validate": True,
                                                   "public_url_template": "https://b.example/{slug}"}),
                   ("POST", "/content/validate"): (422, {"errors": ["no"]})})
    s = HttpContentStore("http://backend/tekjuice", ledger_path=str(tmp_path / "l.db"), token="t" * 40, request=api)
    assert s.update is None and s.public_url("a") == "https://b.example/a"
    with pytest.raises(ContractViolationError):
        s.validate(CONTENT)


def test_validate_without_endpoint_proves_reachability(tmp_path):
    api = FakeApi({("GET", "/content/guide"): (404, None)})
    store(tmp_path, api).validate(CONTENT)
    api.responses[("GET", "/content/guide")] = OSError("down")
    with pytest.raises(ContentStoreError):
        store(tmp_path, api).validate(CONTENT)


def test_unsafe_content_api_configurations_are_refused(tmp_path):
    with pytest.raises(ValueError, match="TOKEN is required"):
        HttpContentStore("https://api.example.com/tekjuice", ledger_path=str(tmp_path / "a"), request=FakeApi(),
                         public_url_template=TEMPLATE)
    with pytest.raises(ValueError, match="must use https"):
        HttpContentStore("http://api.example.com/tekjuice", ledger_path=str(tmp_path / "b"), token="t" * 40,
                         request=FakeApi(), public_url_template=TEMPLATE)
    with pytest.raises(ValueError, match="public URL"):
        HttpContentStore("http://backend/tekjuice", ledger_path=str(tmp_path / "c"), token="t" * 40,
                         request=FakeApi())
    assert is_private_url("http://backend:3000") and is_private_url("http://10.0.0.4/x")
    assert not is_private_url("https://api.example.com")


def test_config_reads_secret_files_and_reports_missing(tmp_path):
    key = tmp_path / "key"
    key.write_text("connector-key\n")
    config = SidecarConfig.from_env({"TEKJUICE_WEBSITE_ID": "w", "TEKJUICE_CONNECTOR_KEY_FILE": str(key),
                                     "TEKJUICE_DATA_ENGINE_URL": "https://e.example",
                                     "TEKJUICE_CONTENT_API_URL": "http://backend/tekjuice",
                                     "TEKJUICE_DATA_DIR": str(tmp_path), "TEKJUICE_LISTEN": "127.0.0.1:9000"})
    assert config.connector_key == "connector-key" and config.port == 9000
    assert config.secret_file == tmp_path / "receiver-secret.json"
    with pytest.raises(ValueError, match="TEKJUICE_CONNECTOR_KEY"):
        SidecarConfig.from_env({"TEKJUICE_WEBSITE_ID": "w"})


def test_sidecar_serves_health_and_readiness_without_secrets(tmp_path, monkeypatch):
    import requests

    from tekjuice_connector.publication import InMemoryContentStore
    from tekjuice_connector.setup import secret_store as secret_store_module
    from tekjuice_connector.sidecar import Sidecar

    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    config = SidecarConfig.from_env({"TEKJUICE_WEBSITE_ID": "w", "TEKJUICE_CONNECTOR_KEY": "k",
                                     "TEKJUICE_DATA_ENGINE_URL": "https://e.example",
                                     "TEKJUICE_CONTENT_API_URL": "http://backend/tekjuice",
                                     "TEKJUICE_DATA_DIR": str(tmp_path), "TEKJUICE_LISTEN": "127.0.0.1:0"})
    sidecar = Sidecar(config, content_store=InMemoryContentStore("https://www.example.com"))
    sidecar.start(run_setup=False)
    try:
        base = f"http://127.0.0.1:{sidecar.port}"
        assert requests.get(base + "/healthz", timeout=5).json() == {"status": "ok"}
        assert requests.get(base + "/readyz", timeout=5).status_code == 503
        # Before setup the receiver rejects everything.
        assert requests.post(base + "/tekjuice/publish", data=b"{}", timeout=5,
                             headers={"Authorization": "Bearer " + "x" * 64}).status_code == 401
        assert requests.post(base + "/elsewhere", data=b"{}", timeout=5).status_code == 404
        secret = "s" * 64
        sidecar.receiver.set_receiver_secrets(secret)
        assert secret not in json.dumps(sidecar.readiness()[1])
    finally:
        sidecar.stop()


def test_site_paths_map_to_discoverability_routes(tmp_path):
    from tekjuice_connector.publication import InMemoryContentStore
    from tekjuice_connector.publication.seo import SeoPages
    from tekjuice_connector.sidecar.server import site_path

    store = InMemoryContentStore("https://www.example.com/blog")
    seo = SeoPages(store, site_url="https://www.example.com", shell_html=lambda: "<html></html>",
                   static_paths=("/", "/pricing"), hub_path="/blog")
    assert site_path(seo, "/sitemap.xml") == "/sitemap.xml"
    assert site_path(seo, "/" + "a" * 32 + ".txt") == "/indexnow/" + "a" * 32
    assert site_path(seo, "/blog/guide") == "/page/guide"
    assert site_path(seo, "/blog/guide.md") == "/markdown/guide"
    assert site_path(seo, "/guide.md") == "/markdown/guide"
    assert site_path(seo, "/pricing") == "/page/pricing" and site_path(seo, "/") == "/page/"
    assert site_path(seo, "/blog") == "/page/blog"
    assert site_path(seo, "/account/settings") is None and site_path(seo, "/blog/a/b") is None


def test_proxy_configs_cover_the_receiver_and_fall_back_to_the_site(tmp_path):
    from tekjuice_connector.sidecar import proxy

    config = SidecarConfig.from_env({
        "TEKJUICE_WEBSITE_ID": "w", "TEKJUICE_CONNECTOR_KEY": "k", "TEKJUICE_DATA_ENGINE_URL": "https://e.example",
        "TEKJUICE_CONTENT_API_URL": "http://backend/tekjuice", "TEKJUICE_DATA_DIR": str(tmp_path),
        "TEKJUICE_API_BASE_URL": "https://api.example.com/v1", "TEKJUICE_SITE_URL": "https://www.example.com",
        "TEKJUICE_STATIC_PATHS": "/,/pricing"})
    nginx = proxy.nginx(config, template="https://www.example.com/blog/{slug}")
    assert "location = /v1/tekjuice/publish" in nginx and "proxy_set_header Authorization $http_authorization" in nginx
    assert "server_name api.example.com" in nginx and "server_name www.example.com" in nginx
    assert "location ^~ /blog/" in nginx and 'location ~ "^/[0-9a-f]{32}\\.txt$"' in nginx
    assert nginx.count("error_page 404 = @tekjuice_site") >= 8
    caddy = proxy.caddy(config, template="https://www.example.com/blog/{slug}")
    assert "handle /v1/tekjuice/publish" in caddy and "rewrite /tekjuice/seo/site{path}" in caddy
    assert "/blog/*" in caddy and "handle_response @missing" in caddy
    # Without discoverability only the receiver is proxied.
    config.site_url = None
    assert "www.example.com" not in proxy.nginx(config)
