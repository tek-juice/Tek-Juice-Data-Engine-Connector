"""TLS and the generated reverse-proxy configuration, for real.

1. The sidecar serving HTTPS itself (PEM files from openssl), with a
   certificate renewed on disk picked up without a restart.
2. Caddy (``caddy:2-alpine`` image, ``tls internal``) running the Caddyfile
   ``tekjuice proxy-config --caddy`` generates, in front of the sidecar, the
   Node.js content API and a stand-in website: the Data Engine's S2S
   requests, discoverability routes and the fallback to the site all go
   through HTTPS with the real host names (curl --resolve).
"""

from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

from tekjuice_connector.setup import secret_store as secret_store_module
from tekjuice_connector.sidecar import Sidecar, SidecarConfig, proxy
from tests.fixtures.publication_fixtures import publish_payload
from tests.fixtures.setup_fixtures import API_BASE_URL, CONNECTOR_KEY, DATA_ENGINE_URL, WEBSITE_ID, FakeDataEngine
from tests.integration.test_sidecar_languages import EXAMPLES, TOKEN, _free_port, _wait_for

SITE_URL = "https://www.customer.example"
TEMPLATE = SITE_URL + "/blog/{slug}"


def _cert(directory: Path, name: str, cn: str) -> tuple:
    cert, key = directory / f"{name}.pem", directory / f"{name}.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", f"/CN={cn}",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    return cert, key


def _served_cn(port: int) -> str:
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    import socket

    with socket.create_connection(("127.0.0.1", port)) as raw, context.wrap_socket(raw) as tls:
        der = tls.getpeercert(binary_form=True)
    out = subprocess.run(["openssl", "x509", "-inform", "der", "-noout", "-subject"], input=der,
                         capture_output=True, check=True).stdout.decode()
    return out.strip().split("CN", 1)[1].lstrip(" =")


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl not installed")
def test_sidecar_serves_https_and_reloads_renewed_certificates(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    cert, key = _cert(tmp_path, "tls", "first.example")
    config = SidecarConfig.from_env({
        "TEKJUICE_WEBSITE_ID": "w", "TEKJUICE_CONNECTOR_KEY": "k", "TEKJUICE_DATA_ENGINE_URL": DATA_ENGINE_URL,
        "TEKJUICE_CONTENT_API_URL": "http://backend/tekjuice", "TEKJUICE_DATA_DIR": str(tmp_path / "d"),
        "TEKJUICE_LISTEN": "127.0.0.1:0", "TEKJUICE_TLS_CERT_FILE": str(cert), "TEKJUICE_TLS_KEY_FILE": str(key),
    })
    from tekjuice_connector.publication import InMemoryContentStore

    sidecar = Sidecar(config, content_store=InMemoryContentStore(SITE_URL), tls_reload_interval=0.2)
    sidecar.start(run_setup=False)
    try:
        assert requests.get(f"https://127.0.0.1:{sidecar.port}/healthz", verify=False, timeout=5).status_code == 200
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(f"http://127.0.0.1:{sidecar.port}/healthz".replace("http:", "https:"), timeout=5,
                         verify=True)
        assert _served_cn(sidecar.port) == "first.example"
        time.sleep(1.1)  # distinct mtime
        _cert(tmp_path, "tls", "renewed.example")
        deadline = time.monotonic() + 5
        while _served_cn(sidecar.port) != "renewed.example" and time.monotonic() < deadline:
            time.sleep(0.2)
        assert _served_cn(sidecar.port) == "renewed.example"
    finally:
        sidecar.stop()


class _Site(BaseHTTPRequestHandler):
    """The customer's website (a single-page app shell)."""

    def do_GET(self):  # noqa: N802
        body = (b"<!doctype html><html><head><title>Customer site</title><meta name='description' "
                b"content='The site'></head><body><div id='root'></div></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _caddy_available() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "image", "inspect", "caddy:2-alpine"], capture_output=True).returncode == 0


def _image(name: str) -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "image", "inspect", name], capture_output=True).returncode == 0


@pytest.mark.parametrize("kind", ["caddy", "nginx"])
def test_generated_proxy_config_fronts_the_sidecar_with_https(tmp_path, monkeypatch, kind):
    image = {"caddy": "caddy:2-alpine", "nginx": "nginx:1.27-alpine"}[kind]
    if not (_image(image) and shutil.which("node") and shutil.which("curl") and shutil.which("openssl")):
        pytest.skip(f"needs the {image} image, node, curl and openssl")
    monkeypatch.setattr(secret_store_module, "in_container", lambda: False)
    https_port, http_port = _free_port(), _free_port()
    site = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    backend_port = _free_port()
    backend = subprocess.Popen(["node", str(EXAMPLES / "node" / "content-api.js")], cwd=tmp_path,
                               env={**os.environ, "TEKJUICE_CONTENT_API_TOKEN": TOKEN, "PUBLIC_URL_TEMPLATE": TEMPLATE,
                                    "PORT": str(backend_port), "CONTENT_FILE": str(tmp_path / "c.json")},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sidecar = caddy = None
    try:
        _wait_for(f"http://127.0.0.1:{backend_port}/tekjuice/capabilities")
        config = SidecarConfig.from_env({
            "TEKJUICE_WEBSITE_ID": WEBSITE_ID, "TEKJUICE_CONNECTOR_KEY": CONNECTOR_KEY,
            "TEKJUICE_DATA_ENGINE_URL": DATA_ENGINE_URL, "TEKJUICE_API_BASE_URL": API_BASE_URL,
            "TEKJUICE_CONTENT_API_URL": f"http://127.0.0.1:{backend_port}/tekjuice",
            "TEKJUICE_CONTENT_API_TOKEN": TOKEN, "TEKJUICE_DATA_DIR": str(tmp_path / "sidecar"),
            "TEKJUICE_LISTEN": "127.0.0.1:0", "TEKJUICE_SITE_URL": SITE_URL,
            "TEKJUICE_SHELL_URL": f"http://127.0.0.1:{site.server_address[1]}/",
            "TEKJUICE_STATIC_PATHS": "/,/pricing", "TEKJUICE_SITE_NAME": "Customer",
        })
        sidecar = Sidecar(config)
        sidecar.start(run_setup=False)
        template = sidecar.content_store.public_url("{slug}")
        upstream, site_upstream = f"127.0.0.1:{sidecar.port}", f"127.0.0.1:{site.server_address[1]}"
        if kind == "caddy":
            caddyfile = ("{\n    auto_https disable_redirects\n    http_port %d\n    https_port %d\n    admin off\n}\n\n"
                         % (http_port, https_port)) + proxy.caddy(
                config, upstream=upstream, site_upstream=site_upstream, template=template, tls_internal=True)
            (tmp_path / "Caddyfile").write_text(caddyfile)
            command = ["docker", "run", "--rm", "--init", "--network", "host", "-v",
                       f"{tmp_path / 'Caddyfile'}:/etc/caddy/Caddyfile:ro", image]
        else:
            certs = tmp_path / "certs"
            certs.mkdir()
            _cert(certs, "site", "customer.example")
            servers = proxy.nginx(config, upstream=f"http://{upstream}", site_upstream=f"http://{site_upstream}",
                                  template=template, listen=str(https_port),
                                  certificate=("/certs/site.pem", "/certs/site.key"))
            (tmp_path / "default.conf").write_text(servers)
            command = ["docker", "run", "--rm", "--init", "--network", "host",
                       "-v", f"{tmp_path / 'default.conf'}:/etc/nginx/conf.d/default.conf:ro",
                       "-v", f"{certs}:/certs:ro", image]
        caddy = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def curl(url, *, method="GET", headers=None, body=None):
            host = url.split("/")[2]
            args = ["curl", "-sk", "-o", "-", "-w", "\n%{http_code}", "--resolve", f"{host}:{https_port}:127.0.0.1",
                    "-X", method, url.replace(host, f"{host}:{https_port}", 1)]
            for name, value in (headers or {}).items():
                args += ["-H", f"{name}: {value}"]
            if body is not None:
                args += ["--data-binary", "@-"]
            out = subprocess.run(args, input=(json.dumps(body).encode() if body is not None else None),
                                 capture_output=True, timeout=30).stdout.decode()
            text, _, status = out.rpartition("\n")
            return int(status or 0), text

        deadline = time.monotonic() + 30
        while curl(SITE_URL + "/")[0] != 200 and time.monotonic() < deadline:
            time.sleep(0.5)

        class Public:
            def post(self, url, headers, payload, timeout):
                status, text = curl(url, method="POST", headers=headers, body=payload)
                try:
                    return status, json.loads(text)
                except ValueError:
                    return status, None

        engine = FakeDataEngine(Public())
        sidecar._setup_options = dict(transport=engine, self_check_post=Public().post,
                                      poll_interval_seconds=0.05, verification_timeout_seconds=10)
        result = sidecar.run_setup_once()
        assert result.ready, result.to_dict()  # S2S through Caddy's HTTPS, Authorization passed through

        status, body = engine.publish(publish_payload(website_id=WEBSITE_ID, slug="caddy-guide", execution_id="c1"))
        assert status in (200, 201), body
        status, page = curl(SITE_URL + "/blog/caddy-guide")
        assert status == 200 and "Moving Cost Guide" in page  # pre-rendered by the sidecar
        status, sitemap = curl(SITE_URL + "/sitemap.xml")
        assert status == 200 and "/blog/caddy-guide" in sitemap
        assert curl(SITE_URL + "/llms.txt")[0] == 200 and curl(SITE_URL + "/robots.txt")[0] == 200
        status, markdown = curl(SITE_URL + "/blog/caddy-guide.md")
        assert status == 200 and "Moving Cost Guide" in markdown
        # Not ours: the site answers.
        status, other = curl(SITE_URL + "/blog/not-published")
        assert status == 200 and "Customer site" in other
        # An approved site change (metadata override) on the customer's own page.
        sidecar.content_store.set_site_override("/pricing", {"meta": {"title": "Pricing that fits"}})
        status, pricing = curl(SITE_URL + "/pricing")
        assert status == 200 and "<title>Pricing that fits</title>" in pricing
        # The IndexNow key file is served on the site.
        from tekjuice_connector.publication.seo import indexnow_key

        key = indexnow_key(sidecar.secret_store.load().active.reveal())
        assert curl(SITE_URL + f"/{key}.txt") == (200, key)
    finally:
        if caddy:
            caddy.terminate()
            try:
                caddy.wait(timeout=10)
            except subprocess.TimeoutExpired:
                caddy.kill()
        if sidecar:
            sidecar.stop()
        backend.terminate()
        backend.wait(timeout=10)
        site.shutdown()
