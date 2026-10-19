"""The nginx change `tekjuice connect` makes, checked by a real nginx (Docker).

The site's server block gets one include; nginx accepts the result, routes
only the receiver and the articles to the connector and still serves the
site's own pages. Skipped without Docker or the nginx:1.27-alpine image.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from tekjuice_connector.connect import webserver

IMAGE = "nginx:1.27-alpine"


def _available() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _available(), reason=f"needs Docker and {IMAGE}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _server(label: str) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            body = f"{label} {self.command} {self.path} auth={self.headers.get('Authorization', '-')}".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_real_nginx_accepts_the_include_and_routes_only_the_connector_paths(tmp_path):
    app, connector = _server("app"), _server("connector")
    port = _free_port()
    site = f"""server {{
    listen {port};
    server_name shop.example;
    location / {{
        proxy_pass http://127.0.0.1:{app.server_address[1]};
    }}
}}
"""
    block = webserver.find_server_block("shop.example", read=lambda p: site, paths=["site.conf"])
    updated = webserver.with_include(block, "shop.example").replace(webserver.INCLUDE_DIR, "/etc/nginx/tekjuice")
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "tekjuice").mkdir()
    (tmp_path / "conf.d" / "site.conf").write_text(updated)
    (tmp_path / "tekjuice" / "shop.example.conf").write_text(webserver.nginx_locations(
        route_path="/tekjuice/publish", articles_path="/articles",
        upstream=f"http://127.0.0.1:{connector.server_address[1]}"))
    name = f"tekjuice-nginx-test-{port}"
    mounts = ["-v", f"{tmp_path / 'conf.d'}:/etc/nginx/conf.d:ro", "-v", f"{tmp_path / 'tekjuice'}:/etc/nginx/tekjuice:ro"]
    check = subprocess.run(["docker", "run", "--rm", *mounts, IMAGE, "nginx", "-t"], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--network", "host", *mounts, IMAGE],
                   check=True, capture_output=True)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                requests.get(base + "/", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.2)
        assert requests.get(base + "/", timeout=5).text.startswith("app GET /")
        assert requests.get(base + "/pricing", timeout=5).text.startswith("app GET /pricing")
        publish = requests.post(base + "/tekjuice/publish", data=b"{}", timeout=5,
                                headers={"Authorization": "Bearer s3cret"})
        assert publish.text == "connector POST /tekjuice/publish auth=Bearer s3cret"
        assert requests.get(base + "/articles", timeout=5).text.startswith("connector GET /articles")
        assert requests.get(base + "/articles/moving-costs", timeout=5).text.startswith("connector GET /articles/moving-costs")
        assert requests.get(base + "/articles-old", timeout=5).text.startswith("app GET /articles-old")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        app.shutdown()
        connector.shutdown()
