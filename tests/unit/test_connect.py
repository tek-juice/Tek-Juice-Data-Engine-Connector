"""`tekjuice connect`: credentials, detection, web-server routing, the whole flow."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tekjuice_connector.connect import environment, service, webserver
from tekjuice_connector.connect.wizard import Connect, ConnectOptions, read_env

KEY = "de_" + "k" * 48
CREDS = {"TEKJUICE_WEBSITE_ID": "site-1", "TEKJUICE_CONNECTOR_KEY": KEY,
         "TEKJUICE_DATA_ENGINE_URL": "https://engine.example"}

NGINX_SITE = """server {
    listen 80;
    server_name shop.example www.shop.example;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl; # managed by Certbot
    server_name shop.example www.shop.example;
    ssl_certificate /etc/letsencrypt/live/shop.example/fullchain.pem;
    location / {
        proxy_pass http://127.0.0.1:3000;
    }
}
"""


# ── Environment detection ─────────────────────────────────────────────────

@pytest.mark.parametrize("files,language,framework", [
    ({"package.json": json.dumps({"dependencies": {"express": "^4", "react": "^18"}})}, "javascript", "Express"),
    ({"package.json": json.dumps({"dependencies": {"next": "14"}})}, "javascript", "Next.js"),
    ({"composer.json": '{"require": {"laravel/framework": "^11"}}'}, "php", "Laravel"),
    ({"Gemfile": "gem 'rails', '~> 7.1'"}, "ruby", "Rails"),
    ({"go.mod": "module x\nrequire github.com/gin-gonic/gin v1.9"}, "go", "Gin"),
    ({"pom.xml": "<artifactId>spring-boot-starter-web</artifactId>"}, "java", "Spring Boot"),
    ({"App.csproj": '<PackageReference Include="Microsoft.AspNetCore.OpenApi" />'}, "dotnet", "ASP.NET Core"),
    ({"requirements.txt": "fastapi==0.110\nuvicorn"}, "python", "FastAPI"),
    ({"requirements.txt": "Django>=5"}, "python", "Django"),
])
def test_detects_languages_and_frameworks(tmp_path, files, language, framework):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    languages, frameworks, _ = environment.detect_application(tmp_path)
    assert language in languages and framework in frameworks


def test_unknown_projects_are_fine(tmp_path):
    assert environment.detect_application(tmp_path) == ([], [], {})
    env = environment.detect(str(tmp_path), run=lambda cmd: SimpleNamespace(returncode=1), which=lambda b: None,
                             exists=lambda p: False)
    assert "any language works" in env.describe()


def test_detects_nginx_and_systemd():
    env = environment.detect(".", run=lambda cmd: SimpleNamespace(returncode=0),
                             which=lambda b: "/usr/sbin/" + b if b in ("nginx", "sudo") else None,
                             exists=lambda p: p in ("/etc/nginx", "/run/systemd/system"))
    assert env.web_server == "nginx" and env.systemd and env.sudo


# ── nginx ─────────────────────────────────────────────────────────────────

def test_finds_the_https_server_block_and_adds_one_include():
    block = webserver.find_server_block("www.shop.example", read=lambda p: NGINX_SITE, paths=["/etc/nginx/conf.d/s.conf"])
    assert block.tls and "ssl_certificate" in block.body
    updated = webserver.with_include(block, "www.shop.example")
    assert updated.count("include /etc/nginx/tekjuice/www.shop.example.conf;") == 1
    assert updated.index("return 301") < updated.index("include /etc/nginx/tekjuice") < updated.index("listen 443 ssl")
    # Idempotent: a second run changes nothing.
    again = webserver.find_server_block("www.shop.example", read=lambda p: updated, paths=["x"])
    assert webserver.with_include(again, "www.shop.example") is None
    assert webserver.find_server_block("other.example", read=lambda p: NGINX_SITE, paths=["x"]) is None


def test_existing_locations_are_reported_not_overwritten():
    text = NGINX_SITE.replace("    location / {", "    location ^~ /articles/ { return 404; }\n    location / {")
    block = webserver.find_server_block("shop.example", read=lambda p: text, paths=["x"])
    assert webserver.conflicts(block, route_path="/tekjuice/publish", articles_path="/articles") == ["/articles/"]


def test_snippet_routes_only_the_receiver_and_articles():
    text = webserver.nginx_locations(route_path="/tekjuice/publish", articles_path="/articles",
                                     upstream="http://127.0.0.1:8787")
    assert "location = /tekjuice/publish" in text and "proxy_set_header Authorization $http_authorization" in text
    assert "location = /articles {" in text and "location ^~ /articles/ {" in text
    assert "location / " not in text            # the site's own routes are untouched
    only_articles = webserver.nginx_locations(route_path=None, articles_path="/insights", upstream="http://x")
    assert "/tekjuice/publish" not in only_articles and "/insights/" in only_articles


def test_service_files():
    unit = service.unit(user="deploy", python="/srv/app/.venv/bin/python")
    assert "User=deploy" in unit and "EnvironmentFile=/etc/tekjuice/connector.env" in unit
    assert "/srv/app/.venv/bin/python -m tekjuice_connector.cli.main serve" in unit and "Restart=always" in unit
    assert service.env_file({"A": "1"}).endswith("A=1\n")
    with pytest.raises(ValueError):
        service.env_file({"A": "1\nB=2"})


# ── The command ───────────────────────────────────────────────────────────

class FakeIO:
    def __init__(self, answers=(), secrets=(), confirm=True):
        self.answers, self.secrets, self.confirm_answer, self.out = list(answers), list(secrets), confirm, []

    def say(self, text=""):
        self.out.append(text)

    def ask(self, prompt, default=None):
        return self.answers.pop(0) if self.answers else (default or "")

    def secret(self, prompt):
        return self.secrets.pop(0)

    def confirm(self, prompt, default=True):
        return self.confirm_answer

    @property
    def text(self):
        return "\n".join(self.out)


class FakeSystem:
    def __init__(self, files, nginx_ok=True):
        self.files, self.nginx_ok, self.commands = dict(files), nginx_ok, []

    def run(self, cmd, input=None, privileged=True):
        self.commands.append(cmd)
        if cmd[:2] == ["cp", "-p"]:
            self.files[cmd[3]] = self.files[cmd[2]]
        ok = not (cmd == ["nginx", "-t"] and not self.nginx_ok)
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, "", "" if ok else "duplicate location")

    def read(self, path):
        if path not in self.files:
            raise OSError(path)
        return self.files[path]

    def exists(self, path):
        return path in self.files

    def write(self, path, text, mode="0644"):
        self.files[path] = text

    def backup(self, path):
        target = path + ".tekjuice-bak-1"
        self.files[target] = self.files[path]
        return target


def _env(**overrides):
    base = dict(project_dir=Path("."), languages=["javascript"], frameworks=["Express"], web_server="nginx",
                systemd=True, docker=False, sudo=True)
    base.update(overrides)
    return environment.Environment(**base)


def _connect(tmp_path, *, io, system=None, env=None, state=None, ready_after=1, receiver_exists=False, **kw):
    env_file = tmp_path / ".env"
    probes = {"ready": 0}

    def get(url):
        if url.endswith("/healthz"):
            return 200, '{"status":"ok"}'
        probes["ready"] += 1
        return (200, '{"ready":true}') if probes["ready"] > ready_after else (503, '{"code":"endpoint_unreachable"}')

    connect = Connect(
        ConnectOptions(env_file=str(env_file), project_dir=str(tmp_path), wait_seconds=60, **kw), io=io,
        system=system or FakeSystem({"/etc/nginx/conf.d/shop.conf": NGINX_SITE}),
        handshake=lambda values: state or SimpleNamespace(domain="shop.example", onboarding_status="BACKEND_CONNECTED",
                                                           ready=False, publication_ready=False),
        detect=lambda d: env or _env(project_dir=Path(d)), get=get,
        post_empty=lambda url: (401, '{"status":"error","error":{"code":"unauthorized"}}') if receiver_exists
        else (404, "<html>not found</html>"),
        sleep=lambda s: None)
    return connect, env_file


@pytest.fixture(autouse=True)
def _nginx_paths(monkeypatch):
    monkeypatch.setattr(webserver, "NGINX_CONFIG_GLOBS", ("/etc/nginx/conf.d/*.conf",))
    monkeypatch.setattr(webserver.glob, "glob", lambda pattern: ["/etc/nginx/conf.d/shop.conf"])


def test_asks_for_missing_credentials_and_saves_them_without_printing_the_key(tmp_path):
    io = FakeIO(answers=["site-1", "https://engine.example"], secrets=[KEY])
    connect, env_file = _connect(tmp_path, io=io, state=SimpleNamespace(
        domain="shop.example", onboarding_status="READY", ready=True, publication_ready=True))
    assert connect.run() == 0
    saved = read_env(env_file)
    assert saved["TEKJUICE_CONNECTOR_KEY"] == KEY and saved["TEKJUICE_WEBSITE_ID"] == "site-1"
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert KEY not in io.text and "Already connected" in io.text


def test_rejects_an_http_data_engine_address(tmp_path):
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in {**CREDS,
                                    "TEKJUICE_DATA_ENGINE_URL": "http://1.2.3.4:9600"}.items()))
    connect, _ = _connect(tmp_path, io=FakeIO())
    assert connect.run() == 2


def test_rejected_credentials_explain_what_to_do(tmp_path):
    class InvalidCredentialsError(Exception):
        pass

    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()))
    io = FakeIO()
    connect, _ = _connect(tmp_path, io=io)
    connect.handshake = lambda values: (_ for _ in ()).throw(InvalidCredentialsError())
    assert connect.run() == 2 and "replace the key" in io.text


def test_creates_the_endpoint_for_any_language_with_nginx(tmp_path):
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()) + "\nOTHER=kept\n")
    io = FakeIO(answers=["https://shop.example", "https://shop.example/articles"])
    system = FakeSystem({"/etc/nginx/conf.d/shop.conf": NGINX_SITE})
    connect, env_file = _connect(tmp_path, io=io, system=system)
    assert connect.run() == 0, io.text

    # Service with its own settings (secret-free output), started and enabled.
    service_env = system.files[service.ENV_PATH]
    assert "TEKJUICE_ARTICLES_URL=https://shop.example/articles" in service_env and f"TEKJUICE_CONNECTOR_KEY={KEY}" in service_env
    assert ["systemctl", "enable", service.SERVICE_NAME] in system.commands
    assert "ExecStart=" in system.files[service.UNIT_PATH]
    # nginx: one include in the site's HTTPS block, a backup, a test, a reload.
    site = system.files["/etc/nginx/conf.d/shop.conf"]
    assert site.count("include /etc/nginx/tekjuice/shop.example.conf;") == 1
    assert "location = /tekjuice/publish" in system.files["/etc/nginx/tekjuice/shop.example.conf"]
    assert system.files["/etc/nginx/conf.d/shop.conf.tekjuice-bak-1"] == NGINX_SITE
    assert ["nginx", "-t"] in system.commands and ["systemctl", "reload", "nginx"] in system.commands
    # The project's .env keeps other lines and remembers the addresses; the key is never printed.
    saved = read_env(env_file)
    assert saved["OTHER"] == "kept" and saved["TEKJUICE_API_BASE_URL"] == "https://shop.example"
    assert KEY not in io.text and "Connected." in io.text and "Express" in io.text


def test_a_failed_nginx_test_is_rolled_back(tmp_path):
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()))
    system = FakeSystem({"/etc/nginx/conf.d/shop.conf": NGINX_SITE}, nginx_ok=False)
    io = FakeIO(answers=["https://shop.example", "https://shop.example/articles"])
    connect, _ = _connect(tmp_path, io=io, system=system, ready_after=10 ** 6)
    connect.options.wait_seconds = 0
    assert connect.run() == 3
    assert system.files["/etc/nginx/conf.d/shop.conf"] == NGINX_SITE         # restored
    assert ["systemctl", "reload", "nginx"] not in system.commands
    assert "undone" in io.text


def test_without_rights_or_systemd_it_prints_exact_steps_and_changes_nothing(tmp_path):
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()))
    system = FakeSystem({"/etc/nginx/conf.d/shop.conf": NGINX_SITE})
    io = FakeIO(answers=["https://api.shop.example", "https://shop.example/articles"])
    connect, _ = _connect(tmp_path, io=io, system=system, env=_env(sudo=False))
    assert connect.run() == 3
    assert system.commands == [] and service.ENV_PATH not in system.files
    assert "tekjuice serve" in io.text and "location = /tekjuice/publish" in io.text


def test_declining_changes_the_server_nothing(tmp_path):
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()))
    system = FakeSystem({"/etc/nginx/conf.d/shop.conf": NGINX_SITE})
    connect, _ = _connect(tmp_path, io=FakeIO(answers=["https://shop.example", "https://shop.example/articles"],
                                              confirm=False), system=system)
    assert connect.run() == 3 and system.commands == []


def test_other_web_servers_get_their_configuration(tmp_path):
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()))
    io = FakeIO(answers=["https://shop.example", "https://shop.example/articles"])
    connect, _ = _connect(tmp_path, io=io, env=_env(web_server="caddy"))
    assert connect.run() == 0
    assert "reverse_proxy 127.0.0.1:8787" in io.text and "handle /tekjuice/publish" in io.text


def test_an_existing_receiver_is_completed_not_duplicated(tmp_path):
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()))
    calls = []
    io = FakeIO(answers=["https://api.shop.example"])
    connect, _ = _connect(tmp_path, io=io, receiver_exists=True)
    connect.existing_setup = lambda **kw: calls.append(kw) or 0
    assert connect.run() == 0
    assert calls == [{"env_file": str(tmp_path / ".env"), "api_base_url": "https://api.shop.example",
                      "route_path": "/tekjuice/publish"}]
    assert "already answers" in io.text


@pytest.mark.parametrize("answers", [["http://shop.example"], ["https://shop.example", "https://shop.example/"]])
def test_invalid_addresses_are_refused(tmp_path, answers):
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()))
    connect, _ = _connect(tmp_path, io=FakeIO(answers=answers))
    assert connect.run() == 2
