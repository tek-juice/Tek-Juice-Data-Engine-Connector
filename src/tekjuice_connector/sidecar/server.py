"""The connector sidecar: S2S publication for a backend in any language.

Runs as its own process or container next to the customer's backend
(``tekjuice serve`` or the ``tekjuice-connector`` Docker image):

* serves the S2S receiver route (``TEKJUICE_ROUTE_PATH``, default
  ``/tekjuice/publish``), which the customer's reverse proxy exposes at
  ``TEKJUICE_API_BASE_URL``;
* hands validated content to the backend's content API
  (:mod:`tekjuice_connector.sidecar.content_api`);
* owns the receiver secret (generated on first start, kept in
  ``TEKJUICE_SECRET_FILE`` on a volume), the endpoint URL, registration with
  the Data Engine, verification, readiness and rotation (the setup in
  :mod:`tekjuice_connector.setup`), retrying in the background until ready;
* ``GET /healthz`` (process alive) and ``GET /readyz`` (200 once the Data
  Engine reports the website ready; the secret-free setup report otherwise).

Configuration is environment variables only (docs/sidecar.md).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

from tekjuice_connector.setup.endpoint import DEFAULT_ROUTE_PATH
from tekjuice_connector.setup.workflow import SetupCode, SetupResult

_log = logging.getLogger("tekjuice_connector.sidecar")

# Setup failures that a retry cannot fix without a configuration change.
PERMANENT_CODES = {
    SetupCode.CONNECTOR_NOT_CONFIGURED, SetupCode.ONBOARDING_AUTH_FAILED, SetupCode.WEBSITE_SCOPE_MISMATCH,
    SetupCode.API_HOST_NOT_DISCOVERABLE, SetupCode.SECRET_PERSISTENCE_UNAVAILABLE,
    SetupCode.ENDPOINT_CHANGE_UNCONFIRMED, SetupCode.ENDPOINT_IDENTITY_MISMATCH,
}


@dataclass
class SidecarConfig:
    website_id: str
    connector_key: str
    data_engine_url: str
    api_base_url: Optional[str]
    # The backend's content API, or None: the connector then keeps published
    # articles itself (standalone) and serves them at ``articles_url``.
    content_api_url: Optional[str]
    content_api_token: Optional[str]
    public_url_template: Optional[str]
    public_content_url: Optional[str]
    route_path: str
    data_dir: Path
    secret_file: Path
    host: str
    port: int
    allow_endpoint_change: bool
    # Discoverability (on when site_url is set): pre-rendered articles,
    # sitemap, robots.txt, llms.txt, feed, Markdown, entity, IndexNow key,
    # and the site changes the Data Engine sends (metadata overrides, redirects).
    site_url: Optional[str] = None
    seo_prefix: str = "/tekjuice/seo"
    shell_url: Optional[str] = None
    site_name: Optional[str] = None
    site_description: Optional[str] = None
    static_paths: tuple = ("/",)
    private_paths: tuple = ()
    hub_path: Optional[str] = None
    hub_title: Optional[str] = None
    hub_description: Optional[str] = None
    # HTTPS served by the sidecar itself (PEM files; reloaded when they change).
    tls_cert_file: Optional[Path] = None
    tls_key_file: Optional[Path] = None
    # Standalone: public base URL of published articles, e.g.
    # https://www.example.com/articles (articles at /articles/<slug>, index
    # at /articles). Needs no code in the backend: the proxy sends that path
    # and the receiver route to the connector.
    articles_url: Optional[str] = None

    @property
    def standalone(self) -> bool:
        return not self.content_api_url

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "SidecarConfig":
        env = dict(os.environ if env is None else env)

        def secret(name: str) -> Optional[str]:
            """``NAME`` or the contents of ``NAME_FILE`` (Docker/Kubernetes secrets)."""
            if env.get(name):
                return env[name]
            path = env.get(f"{name}_FILE")
            if path:
                return Path(path).read_text(encoding="utf-8").strip() or None
            return None

        data_dir = Path(env.get("TEKJUICE_DATA_DIR", "/var/lib/tekjuice"))
        listen = env.get("TEKJUICE_LISTEN", "0.0.0.0:8787")
        host, _, port = listen.rpartition(":")
        missing = [k for k in ("TEKJUICE_WEBSITE_ID", "TEKJUICE_DATA_ENGINE_URL") if not env.get(k)]
        if not env.get("TEKJUICE_CONTENT_API_URL") and not env.get("TEKJUICE_ARTICLES_URL"):
            missing.append("TEKJUICE_CONTENT_API_URL (or TEKJUICE_ARTICLES_URL for standalone)")
        if not secret("TEKJUICE_CONNECTOR_KEY"):
            missing.append("TEKJUICE_CONNECTOR_KEY")
        if missing:
            raise ValueError(f"Missing configuration: {', '.join(missing)}")
        return cls(
            website_id=env["TEKJUICE_WEBSITE_ID"],
            connector_key=secret("TEKJUICE_CONNECTOR_KEY") or "",
            data_engine_url=env["TEKJUICE_DATA_ENGINE_URL"],
            api_base_url=env.get("TEKJUICE_API_BASE_URL"),
            content_api_url=env.get("TEKJUICE_CONTENT_API_URL") or None,
            content_api_token=secret("TEKJUICE_CONTENT_API_TOKEN"),
            public_url_template=env.get("TEKJUICE_PUBLIC_URL_TEMPLATE"),
            public_content_url=env.get("TEKJUICE_PUBLIC_CONTENT_URL"),
            route_path="/" + env.get("TEKJUICE_ROUTE_PATH", DEFAULT_ROUTE_PATH).lstrip("/"),
            data_dir=data_dir,
            secret_file=Path(env.get("TEKJUICE_SECRET_FILE", str(data_dir / "receiver-secret.json"))),
            host=host or "0.0.0.0",
            port=int(port or 8787),
            allow_endpoint_change=env.get("TEKJUICE_ALLOW_ENDPOINT_CHANGE") == "1",
            site_url=env.get("TEKJUICE_SITE_URL") or None,
            seo_prefix="/" + env.get("TEKJUICE_SEO_PREFIX", "/tekjuice/seo").strip("/"),
            shell_url=env.get("TEKJUICE_SHELL_URL") or None,
            site_name=env.get("TEKJUICE_SITE_NAME") or None,
            site_description=env.get("TEKJUICE_SITE_DESCRIPTION") or None,
            static_paths=_paths(env.get("TEKJUICE_STATIC_PATHS"), ("/",)),
            private_paths=_paths(env.get("TEKJUICE_PRIVATE_PATHS"), ()),
            hub_path=env.get("TEKJUICE_HUB_PATH") or None,
            hub_title=env.get("TEKJUICE_HUB_TITLE") or None,
            hub_description=env.get("TEKJUICE_HUB_DESCRIPTION") or None,
            tls_cert_file=Path(env["TEKJUICE_TLS_CERT_FILE"]) if env.get("TEKJUICE_TLS_CERT_FILE") else None,
            tls_key_file=Path(env["TEKJUICE_TLS_KEY_FILE"]) if env.get("TEKJUICE_TLS_KEY_FILE") else None,
            articles_url=_articles_url(env.get("TEKJUICE_ARTICLES_URL")),
        )


def _articles_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
        raise ValueError("TEKJUICE_ARTICLES_URL must be an https URL such as https://www.example.com/articles")
    if parts.path.strip("/") == "":
        raise ValueError("TEKJUICE_ARTICLES_URL needs its own path (e.g. /articles), not the site root")
    return value.strip().rstrip("/")


def _paths(value: Optional[str], default: tuple) -> tuple:
    if not value:
        return default
    return tuple("/" + p.strip().strip("/") if p.strip().strip("/") else "/" for p in value.split(",") if p.strip())


class ShellHtml:
    """The site's HTML shell (its homepage), fetched and cached; the last good
    copy is kept when the site is briefly unreachable."""

    def __init__(self, url: str, ttl: float = 300.0) -> None:
        self._url, self._ttl = url, ttl
        self._html, self._fetched = "", 0.0
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            if self._html and time.monotonic() - self._fetched < self._ttl:
                return self._html
            import urllib.request

            try:
                request = urllib.request.Request(self._url, headers={"User-Agent": "tekjuice-connector"})
                with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - configured https URL
                    self._html = response.read().decode("utf-8", "replace")
                self._fetched = time.monotonic()
            except Exception:  # noqa: BLE001
                _log.warning("Could not fetch the site shell from %s", self._url)
            return self._html or "<!doctype html><html><head></head><body></body></html>"


class Sidecar:
    """Receiver + HTTP server + background setup."""

    def __init__(self, config: SidecarConfig, *, content_store: Any = None, secret_store: Any = None,
                 setup_options: Optional[Dict[str, Any]] = None,
                 retry_delays: tuple = (5, 15, 30, 60, 120, 300, 600),
                 tls_reload_interval: float = 60.0) -> None:
        from tekjuice_connector.publication import PublicationReceiver
        from tekjuice_connector.setup import FileSecretStore
        from tekjuice_connector.sidecar.content_api import HttpContentStore

        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.secret_store = secret_store or FileSecretStore(config.secret_file)
        self.articles = None
        if content_store is None and config.standalone:
            content_store, self.articles = self._standalone(config)
        self.content_store = content_store or HttpContentStore(
            config.content_api_url,
            ledger_path=str(config.data_dir / "ledger.sqlite3"),
            token=config.content_api_token,
            public_url_template=config.public_url_template,
        )
        self.seo = None
        if config.site_url:
            from tekjuice_connector.publication.seo import SeoPages

            self.seo = SeoPages(
                self.content_store, site_url=config.site_url,
                shell_html=ShellHtml(config.shell_url or config.site_url.rstrip("/") + "/"),
                static_paths=config.static_paths, site_name=config.site_name,
                site_description=config.site_description, private_paths=config.private_paths,
                hub_path=config.hub_path, hub_title=config.hub_title, hub_description=config.hub_description,
            )
        self.receiver = PublicationReceiver(store=self.content_store, website_id=config.website_id,
                                            secret_store=self.secret_store, seo=self.seo)
        self._setup_options = setup_options or {}
        self._retry_delays = retry_delays
        self._tls_reload_interval = tls_reload_interval
        self._result: Optional[SetupResult] = None
        self._stop = threading.Event()
        self._server: Optional[ThreadingHTTPServer] = None
        self._setup_thread: Optional[threading.Thread] = None

    @staticmethod
    def _standalone(config: SidecarConfig) -> tuple:
        """The connector's own article store and the pages it serves.

        The pages are not advertised as discoverability capabilities (the
        receiver gets no SeoPages): only the article paths reach the
        connector, so it must not claim the site's sitemap or robots.txt."""
        from tekjuice_connector.publication import SQLiteContentStore
        from tekjuice_connector.publication.seo import SeoPages

        if not config.articles_url:
            raise ValueError("Standalone mode needs TEKJUICE_ARTICLES_URL")
        parts = urlsplit(config.articles_url)
        site = f"{parts.scheme}://{parts.netloc}"
        store = SQLiteContentStore(str(config.data_dir / "content.sqlite3"), config.articles_url)
        pages = SeoPages(store, site_url=site, shell_html=ShellHtml(config.shell_url or site + "/"),
                         static_paths=(), site_name=config.site_name, site_description=config.site_description,
                         hub_path=parts.path.rstrip("/"), hub_title=config.hub_title,
                         hub_description=config.hub_description)
        return store, pages

    # ------------------------------------------------------------------

    @property
    def result(self) -> Optional[SetupResult]:
        return self._result

    def run_setup_once(self, **overrides: Any) -> SetupResult:
        from tekjuice_connector import TekJuiceConnector

        connector = TekJuiceConnector(website_id=self.config.website_id, connector_key=self.config.connector_key,
                                      data_engine_url=self.config.data_engine_url, max_retries=2)
        options = dict(
            secret_store=self.secret_store, receiver=self.receiver, app=None,
            api_base_url=self.config.api_base_url, route_path=self.config.route_path,
            public_content_url=self.config.public_content_url,
            allow_endpoint_change=self.config.allow_endpoint_change,
        )
        options.update(self._setup_options)
        options.update(overrides)
        result = connector.setup(**options)
        self._result = result
        _log.info("Tek Juice setup: %s (endpoint %s)", result.code.value if result.code else None,
                  result.endpoint_url)
        return result

    def _setup_loop(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                result = self.run_setup_once()
            except Exception:  # noqa: BLE001 - keep serving; report and retry
                _log.exception("Tek Juice setup raised")
                result = None
            if result is not None and (result.ready or result.code in PERMANENT_CODES):
                if not result.ready:
                    _log.error("Tek Juice setup stopped: %s; fix the configuration and restart",
                               result.code.value)
                return
            delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
            attempt += 1
            self._stop.wait(delay)

    def readiness(self) -> tuple:
        if self._result is None:
            return 503, {"ready": False, "code": None, "detail": "setup has not completed yet"}
        return (200 if self._result.ready else 503), self._result.to_dict()

    # ------------------------------------------------------------------

    def handler(self) -> type:
        sidecar = self
        route = self.config.route_path.rstrip("/") or "/"
        limit = self.receiver.max_body_bytes
        seo_prefix = self.config.seo_prefix.rstrip("/")
        articles = urlsplit(self.config.articles_url).path.rstrip("/") if self.articles is not None else None

        class Handler(BaseHTTPRequestHandler):
            server_version = "tekjuice-connector"

            def _send(self, status: int, body: Dict[str, Any]) -> None:
                payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
                self._raw(status, payload, {"Content-Type": "application/json"})

            def _raw(self, status: int, payload: bytes, headers: Dict[str, str]) -> None:
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/healthz":
                    return self._send(200, {"status": "ok"})
                if path == "/readyz":
                    return self._send(*sidecar.readiness())
                if sidecar.seo is not None and (path == seo_prefix or path.startswith(seo_prefix + "/")):
                    status, headers, body = seo_response(sidecar.seo, path[len(seo_prefix):])
                    return self._raw(status, body, headers)
                if sidecar.articles is not None and (path == articles or path.startswith(articles + "/")):
                    status, headers, body = seo_response(sidecar.articles, "/site" + path)
                    return self._raw(status, body, headers)
                return self._send(404, {"status": "error", "error": {"code": "not_found"}})

            do_HEAD = do_GET  # noqa: N815 - crawlers check pages with HEAD

            def do_POST(self) -> None:  # noqa: N802
                if self.path.split("?", 1)[0].rstrip("/") != route:
                    return self._send(404, {"status": "error", "error": {"code": "not_found"}})
                length = int(self.headers.get("Content-Length") or 0)
                if length > limit:
                    return self._send(413, {"status": "error", "error": {"code": "request_too_large"}})
                body = self.rfile.read(length)
                result = sidecar.receiver.handle(dict(self.headers.items()), body)
                self._send(result.status_code, result.body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                # The request line only; never headers (they carry the secret).
                _log.debug("%s %s", self.address_string(), format % args)

        return Handler

    def start(self, *, run_setup: bool = True) -> None:
        self._server = ThreadingHTTPServer((self.config.host, self.config.port), self.handler())
        self._server.daemon_threads = True
        if self.config.tls_cert_file or self.config.tls_key_file:
            self._enable_tls()
        threading.Thread(target=self._server.serve_forever, name="tekjuice-http", daemon=True).start()
        if run_setup:
            self._setup_thread = threading.Thread(target=self._setup_loop, name="tekjuice-setup", daemon=True)
            self._setup_thread.start()

    def _enable_tls(self) -> None:
        import ssl

        cert, key = self.config.tls_cert_file, self.config.tls_key_file
        if not (cert and key):
            raise ValueError("Set both TEKJUICE_TLS_CERT_FILE and TEKJUICE_TLS_KEY_FILE")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert), str(key))
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        stamp = (cert.stat().st_mtime, key.stat().st_mtime)

        def reload() -> None:
            # Renewed certificates (certbot, cert-manager, ...) are picked up
            # without a restart: new handshakes use the reloaded chain.
            nonlocal stamp
            while not self._stop.wait(self._tls_reload_interval):
                try:
                    current = (cert.stat().st_mtime, key.stat().st_mtime)
                    if current != stamp:
                        context.load_cert_chain(str(cert), str(key))
                        stamp = current
                        _log.info("TLS certificate reloaded")
                except (OSError, ssl.SSLError):
                    _log.warning("TLS certificate reload failed; keeping the current one")

        threading.Thread(target=reload, name="tekjuice-tls-reload", daemon=True).start()

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else self.config.port

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def serve_forever(self, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.start()
        try:
            while True:
                sleep(3600)
        except KeyboardInterrupt:
            self.stop()


def seo_response(seo: Any, subpath: str) -> tuple:
    """(status, headers, body) for a discoverability path under the SEO
    prefix; the same routes as ``adapters.fastapi.create_seo_router``."""
    text = "text/plain; charset=utf-8"
    site_wide = {
        "/sitemap.xml": (seo.sitemap, "application/xml"), "/robots.txt": (seo.robots_txt, text),
        "/llms.txt": (seo.llms_txt, text), "/llms-full.txt": (seo.llms_full_txt, text),
        "/feed.json": (seo.feed_json, "application/feed+json"),
    }
    not_found = (404, {"Content-Type": "text/plain"}, b"")
    if subpath.startswith("/site/") or subpath == "/site":
        mapped = site_path(seo, subpath[len("/site"):] or "/")
        return seo_response(seo, mapped) if mapped else not_found
    if subpath in site_wide:
        method, media = site_wide[subpath]
        return 200, {"Content-Type": media, "Cache-Control": "public, max-age=900"}, method().encode("utf-8")
    if subpath == "/home":
        # The site's home page with the business's identity JSON-LD (map the
        # web server's "/" here, falling back to the site's own page).
        body = seo.home()
        return (200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=300"},
                body.encode("utf-8")) if body is not None else not_found
    if subpath == "/entity.json":
        body = seo.entity_json()
        return (200, {"Content-Type": "application/json", "Cache-Control": "public, max-age=900"},
                body.encode("utf-8")) if body is not None else not_found
    if subpath == "/site-overrides.json":
        # For backends that render pages server-side and apply approved
        # metadata/redirects themselves.
        lister = getattr(seo._store, "list_site_overrides", None)  # noqa: SLF001
        body = json.dumps(lister() if callable(lister) else {})
        return 200, {"Content-Type": "application/json", "Cache-Control": "public, max-age=60"}, body.encode()
    if subpath.startswith("/indexnow/"):
        body = seo.indexnow_key_file(subpath[len("/indexnow/"):])
        return (200, {"Content-Type": text, "Cache-Control": "public, max-age=86400"},
                body.encode("utf-8")) if body is not None else not_found
    for prefix, method, media in (("/page", seo.page, "text/html; charset=utf-8"),
                                  ("/markdown", seo.markdown, "text/markdown; charset=utf-8")):
        if subpath == prefix or subpath.startswith(prefix + "/"):
            slug = subpath[len(prefix):].strip("/")
            if method == seo.page:
                redirect = seo.redirect_for(slug)
                if redirect:
                    return redirect[1], {"Location": redirect[0], "Cache-Control": "public, max-age=300"}, b""
            elif not slug:
                return not_found
            body = method(slug)
            if body is None:
                return not_found
            canonical = seo.canonical_url(slug)
            if method == seo.page:
                meta = (seo._override("/" + slug if slug else "/") or {}).get("meta") or {}  # noqa: SLF001
                canonical = meta.get("canonical") or canonical
            return 200, {"Content-Type": media, "Cache-Control": "public, max-age=300",
                         "Link": f'<{canonical}>; rel="canonical"'}, body.encode("utf-8")
    return not_found


_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_INDEXNOW = re.compile(r"^/([0-9a-f]{32})\.txt$")


def article_prefix(seo: Any) -> str:
    """Path prefix of published articles (``/blog/``), from the public URL template."""
    path = urlsplit(seo.canonical_url("tekjuice-slug")).path
    return path[: path.rindex("tekjuice-slug")] if "tekjuice-slug" in path else "/"


def site_path(seo: Any, path: str) -> Optional[str]:
    """The SEO route serving the website path *path*, or None.

    Lets a proxy forward the site's own paths unchanged
    (``/tekjuice/seo/site{path}``) without rewriting them itself."""
    path = "/" + path.lstrip("/")
    if path.lstrip("/") in ("sitemap.xml", "robots.txt", "llms.txt", "llms-full.txt", "feed.json"):
        return path
    match = _INDEXNOW.match(path)
    if match:
        return f"/indexnow/{match.group(1)}"
    prefix = article_prefix(seo)
    for base in dict.fromkeys((prefix, "/")):
        if path.startswith(base):
            rest = path[len(base):]
            if rest.endswith(".md") and _SLUG.match(rest[:-3]):
                return f"/markdown/{rest[:-3]}"
            if base == prefix and _SLUG.match(rest):
                return f"/page/{rest}"
    if path.rstrip("/") in [p.rstrip("/") for p in getattr(seo, "_static", ())] or path == "/":
        return "/page" + (path if path != "/" else "/")
    hub = getattr(seo, "_hub", None)
    if hub and path.rstrip("/") == hub.rstrip("/"):
        return "/page" + hub
    return None
