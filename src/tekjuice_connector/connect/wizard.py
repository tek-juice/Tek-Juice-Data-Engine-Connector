"""``tekjuice connect``: connect this server to Tek Juice in one command.

Run it in the backend's directory after ``pip install tekjuice-connector``:

1. reads ``TEKJUICE_WEBSITE_ID``, ``TEKJUICE_CONNECTOR_KEY`` and
   ``TEKJUICE_DATA_ENGINE_URL`` from ``.env`` and asks for any that are
   missing (the key without echo), then saves them there;
2. authenticates with the Data Engine (the handshake); a website that is
   already connected and ready is left as it is;
3. detects the application (language, framework), the web server and the
   service manager, for information: what it installs does not depend on
   the language the application is written in;
4. asks for what it cannot discover: the public HTTPS address where the
   Data Engine reaches this server and where published articles appear;
5. finds an existing Tek Juice receiver at that address (in-process
   connector) and completes its setup; otherwise creates the endpoint: the
   connector runs as a service beside the application (systemd), keeps the
   receiver secret and the published articles itself, and the web server
   routes ``/tekjuice/publish`` and the articles path to it. Nothing in the
   application's code changes;
6. waits until the Data Engine has verified the endpoint (READY).

It asks before changing the server (``--yes`` accepts), backs up every file
it edits and restores it when the web server's configuration test fails.
Secrets are never printed. Exit codes: 0 connected, 1 failed, 2 invalid
input, 3 waiting for a step that needs the operator.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from tekjuice_connector.connect import environment, service, webserver
from tekjuice_connector.setup.endpoint import DEFAULT_ROUTE_PATH, EndpointError, validate_base_url

REQUIRED = ("TEKJUICE_WEBSITE_ID", "TEKJUICE_CONNECTOR_KEY", "TEKJUICE_DATA_ENGINE_URL")
UPSTREAM = f"http://{service.LISTEN}"


class ConsoleIO:
    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def ask(self, prompt: str, default: Optional[str] = None) -> str:
        suffix = f" [{default}]" if default else ""
        value = input(f"{prompt}{suffix}: ").strip()
        return value or (default or "")

    def secret(self, prompt: str) -> str:
        return getpass.getpass(f"{prompt} (input hidden): ").strip()

    def confirm(self, prompt: str, default: bool = True) -> bool:
        answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer in ("y", "yes")


class System:
    """Privileged file and service operations (sudo when not root)."""

    def __init__(self, *, use_sudo: bool) -> None:
        self._prefix = ["sudo", "-n"] if use_sudo and os.geteuid() != 0 else []

    def run(self, cmd: List[str], *, input: Optional[str] = None, privileged: bool = True) -> subprocess.CompletedProcess:
        full = (self._prefix if privileged else []) + cmd
        return subprocess.run(full, input=input, capture_output=True, text=True, timeout=120)

    def read(self, path: str) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except PermissionError:
            done = self.run(["cat", path])
            if done.returncode:
                raise OSError(done.stderr.strip())
            return done.stdout

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def write(self, path: str, text: str, mode: str = "0644") -> None:
        for cmd, data in ((["mkdir", "-p", os.path.dirname(path)], None), (["tee", path], text),
                          (["chmod", mode, path], None)):
            done = self.run(cmd, input=data)
            if done.returncode:
                raise OSError(f"{' '.join(cmd[:2])}: {done.stderr.strip()}")

    def backup(self, path: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = f"{path}.tekjuice-bak-{stamp}"
        done = self.run(["cp", "-p", path, target])
        if done.returncode:
            raise OSError(done.stderr.strip())
        return target


def http_get(url: str, timeout: float = 10.0) -> tuple:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tekjuice-connect"}),
                                    timeout=timeout) as response:  # noqa: S310 - operator-provided URL
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return None, str(exc)


def http_post_empty(url: str, timeout: float = 10.0) -> tuple:
    request = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "tekjuice-connect"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return None, str(exc)


def default_handshake(values: Dict[str, str]) -> Any:
    from tekjuice_connector import TekJuiceConnector

    connector = TekJuiceConnector(website_id=values["TEKJUICE_WEBSITE_ID"],
                                  connector_key=values["TEKJUICE_CONNECTOR_KEY"],
                                  data_engine_url=values["TEKJUICE_DATA_ENGINE_URL"], max_retries=2)
    return connector.connect()


# ── .env handling ─────────────────────────────────────────────────────────


def read_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def update_env(path: Path, updates: Dict[str, str]) -> None:
    """Set keys in the dotenv file (others kept), mode 0600."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip().removeprefix("export ").strip() if "=" in line else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    tmp = path.with_name(f".{path.name}.tekjuice-tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ── The command ───────────────────────────────────────────────────────────


@dataclass
class ConnectOptions:
    env_file: str = ".env"
    project_dir: str = "."
    assume_yes: bool = False
    wait_seconds: float = 300.0
    route_path: str = DEFAULT_ROUTE_PATH


@dataclass
class Connect:
    options: ConnectOptions = field(default_factory=ConnectOptions)
    io: Any = field(default_factory=ConsoleIO)
    system: Optional[System] = None
    handshake: Callable[[Dict[str, str]], Any] = default_handshake
    detect: Callable[..., environment.Environment] = environment.detect
    get: Callable[[str], tuple] = http_get
    post_empty: Callable[[str], tuple] = http_post_empty
    existing_setup: Optional[Callable[..., int]] = None
    sleep: Callable[[float], None] = time.sleep

    # Steps ----------------------------------------------------------------
    def credentials(self) -> Optional[Dict[str, str]]:
        path = Path(self.options.env_file)
        values = read_env(path)
        missing = [k for k in REQUIRED if not values.get(k)]
        if missing:
            self.io.say(f"{path} is missing {', '.join(missing)}. Paste them from the Tek Juice setup page.")
            for key in missing:
                values[key] = (self.io.secret(key) if key == "TEKJUICE_CONNECTOR_KEY" else self.io.ask(key)).strip()
                if not values[key]:
                    self.io.say(f"{key} is required.")
                    return None
        url = values["TEKJUICE_DATA_ENGINE_URL"]
        if urlsplit(url).scheme != "https":
            self.io.say("TEKJUICE_DATA_ENGINE_URL must be the https address shown on the setup page.")
            return None
        if missing:
            from tekjuice_connector.setup.secret_store import git_exposure

            exposure = git_exposure(path.resolve()) if path.exists() or path.parent.exists() else None
            if exposure:
                self.io.say(f"Not saving to {path}: {exposure}. The values are used for this run only.")
            else:
                update_env(path, {k: values[k] for k in missing})
                self.io.say(f"Saved to {path} (mode 600).")
        return values

    def run(self) -> int:
        io = self.io
        from tekjuice_connector.version import __version__

        io.say(f"Tek Juice connector {__version__}: connecting this server")
        values = self.credentials()
        if values is None:
            return 2

        io.say("\n[1/5] Authenticating with the Data Engine")
        try:
            state = self.handshake(values)
        except Exception as exc:  # noqa: BLE001 - reported to the operator, never with secrets
            name = type(exc).__name__
            if "Credential" in name or "Mismatch" in name:
                io.say(f"  The Data Engine rejected the website ID or connector key ({name}). "
                       "Copy them again from the setup page (or replace the key there).")
                return 2
            io.say(f"  The Data Engine could not be reached ({name}). Check TEKJUICE_DATA_ENGINE_URL and the network.")
            return 1
        domain = getattr(state, "domain", None) or ""
        io.say(f"  Authenticated for {domain or 'this website'} (status {getattr(state, 'onboarding_status', '?')}).")
        if getattr(state, "ready", False) and getattr(state, "publication_ready", False):
            io.say("  Already connected: publication is verified and the website is READY. Nothing to do.")
            return 0

        io.say("\n[2/5] Detecting this server")
        env = self.detect(self.options.project_dir)
        io.say(f"  {env.describe()}")

        io.say("\n[3/5] Public addresses")
        api_base = self._ask_url(values, "TEKJUICE_API_BASE_URL",
                                 "Public https address where Tek Juice reaches this server",
                                 f"https://{domain}" if domain else None, base=True)
        if api_base is None:
            return 2
        endpoint = api_base + self.options.route_path
        status, body = self.post_empty(endpoint)
        if status == 401 and '"unauthorized"' in (body or ""):
            io.say(f"  A Tek Juice receiver already answers at {endpoint}: completing its setup.")
            return self._existing(api_base)

        site = f"https://{domain}" if domain else api_base
        articles = self._ask_url(values, "TEKJUICE_ARTICLES_URL", "Where published articles should appear",
                                 f"{site}/articles", base=False)
        if articles is None:
            return 2
        update_env(Path(self.options.env_file), {"TEKJUICE_API_BASE_URL": api_base, "TEKJUICE_ARTICLES_URL": articles})

        io.say("\n[4/5] Creating the publication endpoint")
        return self._create(values, env, api_base, articles)

    # Helpers ----------------------------------------------------------------
    def _ask_url(self, values: Dict[str, str], key: str, prompt: str, default: Optional[str], *,
                 base: bool) -> Optional[str]:
        current = values.get(key) or default
        value = current if (self.options.assume_yes and current) else self.io.ask(f"  {prompt}", current)
        value = value.strip().rstrip("/")
        try:
            if base:
                return validate_base_url(value)
            parts = urlsplit(value)
            validate_base_url(f"{parts.scheme}://{parts.netloc}")
            if not parts.path.strip("/"):
                raise EndpointError("articles need their own path, e.g. /articles")
            return value
        except EndpointError as exc:
            self.io.say(f"  {value or '(empty)'}: {exc}")
            return None

    def _existing(self, api_base: str) -> int:
        from tekjuice_connector.cli.setup import run_setup

        setup = self.existing_setup or run_setup
        return setup(env_file=self.options.env_file, api_base_url=api_base, route_path=self.options.route_path)

    def _create(self, values: Dict[str, str], env: environment.Environment, api_base: str, articles: str) -> int:
        io = self.io
        api_host = urlsplit(api_base).hostname or ""
        site_host = urlsplit(articles).hostname or ""
        articles_path = urlsplit(articles).path.rstrip("/")
        io.say("  The connector will run beside your application (any language): it keeps the receiver")
        io.say("  secret and the published articles itself. Your application's code is not changed.")
        io.say(f"  - service {service.SERVICE_NAME} (systemd), listening on {service.LISTEN}")
        io.say(f"  - {api_base}{self.options.route_path} -> the connector (the Data Engine publishes here)")
        io.say(f"  - {articles} and {articles}/<article> -> the connector (published articles)")
        if not env.systemd or not env.sudo:
            return self._manual(values, api_base, articles, env,
                                "no systemd on this server" if not env.systemd else "administrator rights (sudo) are needed")
        if not (self.options.assume_yes or io.confirm("  Apply these changes to this server?")):
            return self._manual(values, api_base, articles, env, "changes declined")
        system = self.system or System(use_sudo=True)

        service_env = {k: values[k] for k in REQUIRED}
        service_env.update({"TEKJUICE_API_BASE_URL": api_base, "TEKJUICE_ARTICLES_URL": articles,
                            "TEKJUICE_ROUTE_PATH": self.options.route_path, "TEKJUICE_DATA_DIR": service.DATA_DIR,
                            "TEKJUICE_LISTEN": service.LISTEN})
        try:
            system.write(service.ENV_PATH, service.env_file(service_env), mode="0600")
            user = os.environ.get("SUDO_USER") or getpass.getuser()
            system.write(service.UNIT_PATH, service.unit(user=user, python=sys.executable))
            for cmd in (["systemctl", "daemon-reload"], ["systemctl", "enable", service.SERVICE_NAME],
                        ["systemctl", "restart", service.SERVICE_NAME]):
                done = system.run(cmd)
                if done.returncode:
                    raise OSError(f"{' '.join(cmd)}: {done.stderr.strip()}")
        except OSError as exc:
            io.say(f"  Could not install the service: {exc}")
            return 1
        io.say(f"  Service {service.SERVICE_NAME} installed and started.")
        if not self._wait_local():
            io.say(f"  The service did not start; see: sudo journalctl -u {service.SERVICE_NAME} -n 50")
            return 1

        routed = self._route(system, env, api_host, site_host, articles_path)
        if routed:
            # Set up again now that the public route reaches the connector.
            system.run(["systemctl", "restart", service.SERVICE_NAME])
            self._wait_local()
        io.say("\n[5/5] Waiting for the Data Engine to verify the endpoint")
        ready = self._wait_ready()
        if ready is True:
            io.say(f"\nConnected. Tek Juice publishes to {api_base}{self.options.route_path};")
            io.say(f"articles appear at {articles}/<article>. The website is READY in the dashboard.")
            return 0
        io.say(f"  Not ready yet: {ready}.")
        if not routed:
            io.say("  Add the web-server configuration shown above; the service keeps retrying on its own.")
        io.say(f"  Progress: curl -s http://{service.LISTEN}/readyz   Logs: sudo journalctl -u {service.SERVICE_NAME}")
        return 3

    def _route(self, system: System, env: environment.Environment, api_host: str, site_host: str,
               articles_path: str) -> bool:
        io = self.io
        plan = {api_host: {"route_path": self.options.route_path, "articles_path": None}}
        plan.setdefault(site_host, {"route_path": None, "articles_path": None})["articles_path"] = articles_path
        if env.web_server != "nginx":
            io.say(f"  Web server: {env.web_server or 'none found'}. Add this configuration, then reload it:")
            for host, paths in plan.items():
                if env.web_server == "caddy":
                    text = webserver.caddy_handles(route_path=paths["route_path"] or "/tekjuice/__none__",
                                                   articles_path=paths["articles_path"] or "/__none__",
                                                   upstream=UPSTREAM)
                elif env.web_server == "apache":
                    text = webserver.apache_directives(route_path=paths["route_path"] or "/tekjuice/__none__",
                                                       articles_path=paths["articles_path"] or "/__none__",
                                                       upstream=UPSTREAM)
                else:
                    text = webserver.nginx_locations(upstream=UPSTREAM, **paths)
                io.say(f"\n  # in the HTTPS site for {host}\n{text}")
            return False
        for host, paths in plan.items():
            block = webserver.find_server_block(host, read=system.read)
            snippet = webserver.nginx_locations(upstream=UPSTREAM, **paths)
            if block is None:
                io.say(f"  No nginx server block for {host} was found. Add inside its HTTPS server block:\n{snippet}")
                return False
            clash = webserver.conflicts(block, **paths)
            if clash:
                io.say(f"  {block.path} already defines {', '.join(clash)} for {host}: not changed. "
                       f"Remove those locations or choose another articles path, then rerun.")
                return False
            try:
                system.write(f"{webserver.INCLUDE_DIR}/{host}.conf", snippet)
                updated = webserver.with_include(block, host)
                backup = None
                if updated is not None:
                    backup = system.backup(block.path)
                    system.write(block.path, updated)
                test = system.run(["nginx", "-t"])
                if test.returncode:
                    if backup:
                        system.run(["cp", "-p", backup, block.path])
                    io.say(f"  nginx rejected the change, so it was undone ({backup or 'no file edited'}):\n"
                           f"  {test.stderr.strip()[-400:]}")
                    return False
                reload = system.run(["systemctl", "reload", "nginx"])
                if reload.returncode:
                    system.run(["nginx", "-s", "reload"])
            except OSError as exc:
                io.say(f"  Could not configure nginx for {host}: {exc}")
                return False
            io.say(f"  nginx: {host} routes {', '.join(p for p in (paths['route_path'], paths['articles_path']) if p)} "
                   f"to the connector" + (f" (backup {backup})" if backup else " (already included)"))
        return True

    def _wait_local(self, attempts: int = 30) -> bool:
        for _ in range(attempts):
            status, _ = self.get(f"http://{service.LISTEN}/healthz")
            if status == 200:
                return True
            self.sleep(1)
        return False

    def _wait_ready(self) -> Any:
        deadline = time.monotonic() + self.options.wait_seconds
        last = None
        while True:
            status, body = self.get(f"http://{service.LISTEN}/readyz")
            if status == 200:
                return True
            try:
                info = json.loads(body or "{}")
            except ValueError:
                info = {}
            code = info.get("code") or info.get("detail") or "setup running"
            if code != last:
                self.io.say(f"  ... {code}")
                last = code
            if time.monotonic() >= deadline:
                return code
            self.sleep(5)

    def _manual(self, values: Dict[str, str], api_base: str, articles: str, env: environment.Environment,
                reason: str) -> int:
        io = self.io
        io.say(f"\n  Not changing this server ({reason}). To finish by hand:")
        io.say("  1. Keep the connector running with these settings (e.g. as a service):")
        io.say(f"       TEKJUICE_API_BASE_URL={api_base} TEKJUICE_ARTICLES_URL={articles} "
               f"TEKJUICE_DATA_DIR=<persistent dir> TEKJUICE_LISTEN={service.LISTEN} tekjuice serve")
        io.say("     (it also reads TEKJUICE_WEBSITE_ID, TEKJUICE_CONNECTOR_KEY and TEKJUICE_DATA_ENGINE_URL)")
        io.say("  2. Route these paths to it in your web server:")
        io.say(webserver.nginx_locations(route_path=self.options.route_path,
                                         articles_path=urlsplit(articles).path, upstream=UPSTREAM))
        io.say("  Then run `tekjuice connect` again to check.")
        return 3


def run_connect(*, env_file: str = ".env", project_dir: str = ".", assume_yes: bool = False) -> int:
    return Connect(ConnectOptions(env_file=env_file, project_dir=project_dir, assume_yes=assume_yes)).run()
