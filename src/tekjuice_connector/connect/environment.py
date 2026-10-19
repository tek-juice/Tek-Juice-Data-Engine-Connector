"""What the customer's server runs: language, framework, web server, services.

Informational: ``tekjuice connect`` installs the same endpoint whatever the
application is written in (the connector runs beside it, not inside it).
Detection only decides what to tell the operator and which web-server
configuration to offer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# (language, marker file, {dependency substring: framework})
_MARKERS = (
    ("python", ("pyproject.toml", "requirements.txt", "Pipfile", "setup.py", "manage.py"),
     {"fastapi": "FastAPI", "django": "Django", "flask": "Flask", "starlette": "Starlette"}),
    ("javascript", ("package.json",),
     {"next": "Next.js", "@nestjs/core": "NestJS", "nuxt": "Nuxt", "express": "Express", "fastify": "Fastify",
      "koa": "Koa", "@remix-run": "Remix", "@sveltejs/kit": "SvelteKit", "vite": "Vite"}),
    ("php", ("composer.json", "wp-config.php", "index.php"),
     {"laravel/framework": "Laravel", "symfony/": "Symfony", "wp-config.php": "WordPress"}),
    ("ruby", ("Gemfile",), {"rails": "Rails", "sinatra": "Sinatra"}),
    ("go", ("go.mod",), {"gin-gonic": "Gin", "labstack/echo": "Echo", "gofiber": "Fiber"}),
    ("java", ("pom.xml", "build.gradle", "build.gradle.kts"), {"spring-boot": "Spring Boot", "quarkus": "Quarkus"}),
    ("dotnet", ("*.csproj", "*.sln"), {"Microsoft.AspNetCore": "ASP.NET Core"}),
    ("elixir", ("mix.exs",), {"phoenix": "Phoenix"}),
    ("rust", ("Cargo.toml",), {"actix-web": "Actix", "axum": "Axum", "rocket": "Rocket"}),
)
_DEPENDENCY_FILES = ("pyproject.toml", "requirements.txt", "Pipfile", "package.json", "composer.json", "Gemfile",
                     "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "mix.exs", "Cargo.toml")


@dataclass
class Environment:
    project_dir: Path
    languages: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    web_server: Optional[str] = None            # "nginx" | "caddy" | "apache" | None
    systemd: bool = False
    docker: bool = False
    sudo: bool = False
    evidence: Dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        app = ", ".join(self.frameworks or self.languages) or "unrecognised (any language works)"
        return (f"application: {app}; web server: {self.web_server or 'none found'}; "
                f"services: {'systemd' if self.systemd else 'no systemd'}"
                f"{', docker' if self.docker else ''}")


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def detect_application(project_dir: Path) -> tuple:
    """(languages, frameworks, evidence) from the files in *project_dir*."""
    languages: List[str] = []
    frameworks: List[str] = []
    evidence: Dict[str, str] = {}
    texts = {name: _read(project_dir / name) for name in _DEPENDENCY_FILES if (project_dir / name).is_file()}
    package = texts.get("package.json")
    if package:
        try:
            data = json.loads(package)
            texts["package.json"] = " ".join(list((data.get("dependencies") or {}).keys())
                                             + list((data.get("devDependencies") or {}).keys()))
        except ValueError:
            pass
    for language, markers, known in _MARKERS:
        found = [m for m in markers if (list(project_dir.glob(m)) if "*" in m else (project_dir / m).exists())]
        if not found:
            continue
        languages.append(language)
        evidence[language] = found[0]
        haystack = " ".join(texts.get(m, "") for m in markers) + " " + " ".join(found)
        for csproj in project_dir.glob("*.csproj") if language == "dotnet" else ():
            haystack += _read(csproj)
        for needle, name in known.items():
            pattern = re.escape(needle) if not needle.isalpha() else rf"(?<![\w-]){re.escape(needle)}(?![\w-])"
            if re.search(pattern, haystack, re.IGNORECASE) and name not in frameworks:
                frameworks.append(name)
    return languages, frameworks, evidence


def _running(name: str, run: Callable) -> bool:
    try:
        return run(["pgrep", "-x", name]).returncode == 0
    except (OSError, FileNotFoundError):
        return False


def detect(project_dir: str = ".", *, run: Optional[Callable] = None, which: Callable = shutil.which,
           exists: Callable[[str], bool] = os.path.exists) -> Environment:
    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=10))
    directory = Path(project_dir).resolve()
    languages, frameworks, evidence = detect_application(directory)
    web = None
    for name, binary, config in (("nginx", "nginx", "/etc/nginx"), ("caddy", "caddy", "/etc/caddy"),
                                 ("apache", "apache2", "/etc/apache2"), ("apache", "httpd", "/etc/httpd")):
        if which(binary) and (exists(config) or _running(binary, run)):
            web = name
            break
    systemd = exists("/run/systemd/system")
    sudo = False
    if which("sudo"):
        try:
            sudo = run(["sudo", "-n", "true"]).returncode == 0
        except (OSError, subprocess.SubprocessError):
            sudo = False
    return Environment(project_dir=directory, languages=languages, frameworks=frameworks, web_server=web,
                       systemd=systemd, docker=bool(which("docker")), sudo=sudo or os.geteuid() == 0,
                       evidence=evidence)
