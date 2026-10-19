"""Route the receiver and the article pages to the connector, in the site's web server.

nginx is configured automatically, with consent: the connector's locations
go into ``/etc/nginx/tekjuice/<host>.conf`` and one ``include`` line is
added to the site's existing HTTPS ``server`` block. The edited file is
backed up first; when ``nginx -t`` fails the backup is restored and nothing
is reloaded. Caddy and Apache get the exact configuration to add.

Only two paths are routed: the receiver (``/tekjuice/publish``) and the
articles path (``/articles`` and ``/articles/<slug>``). Everything else the
site serves is untouched.
"""

from __future__ import annotations

import glob
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

INCLUDE_DIR = "/etc/nginx/tekjuice"
NGINX_CONFIG_GLOBS = ("/etc/nginx/conf.d/*.conf", "/etc/nginx/sites-enabled/*", "/etc/nginx/nginx.conf")
MARKER = "# Tek Juice connector (managed by `tekjuice connect`)"


def nginx_locations(*, route_path: Optional[str], articles_path: Optional[str], upstream: str) -> str:
    """The connector's locations for one server block (the receiver, the articles, or both)."""
    upstream = upstream.rstrip("/")
    common = ("    proxy_set_header Host $host;\n"
              "    proxy_set_header X-Forwarded-Proto $scheme;\n"
              "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n")
    out = [MARKER, "# Rerun `tekjuice connect` to update; remove the include line to disconnect."]
    if route_path:
        out.append(f"location = {route_path} {{\n    proxy_pass {upstream};\n"
                   f"    proxy_set_header Authorization $http_authorization;\n{common}"
                   "    client_max_body_size 3m;\n    proxy_read_timeout 60s;\n}")
    if articles_path:
        articles = "/" + articles_path.strip("/")
        out.append(f"location = {articles} {{\n    proxy_pass {upstream};\n{common}}}")
        out.append(f"location ^~ {articles}/ {{\n    proxy_pass {upstream};\n{common}}}")
    return "\n".join(out) + "\n"


def caddy_handles(*, route_path: str, articles_path: str, upstream: str) -> str:
    articles = "/" + articles_path.strip("/")
    target = upstream.replace("http://", "").rstrip("/")
    return (f"    # {MARKER[2:]}\n    handle {route_path} {{\n        reverse_proxy {target}\n    }}\n"
            f"    handle {articles} {articles}/* {{\n        reverse_proxy {target}\n    }}\n")


def apache_directives(*, route_path: str, articles_path: str, upstream: str) -> str:
    articles = "/" + articles_path.strip("/")
    target = upstream.rstrip("/")
    return (f"    # {MARKER[2:]} (needs mod_proxy and mod_proxy_http)\n"
            f"    ProxyPreserveHost On\n"
            f"    ProxyPass {route_path} {target}{route_path}\n"
            f"    ProxyPass {articles} {target}{articles}\n"
            f"    ProxyPassReverse {articles} {target}{articles}\n")


@dataclass
class ServerBlock:
    path: str
    text: str
    start: int            # index of "server"
    open_brace: int
    end: int              # index of the closing brace
    tls: bool

    @property
    def body(self) -> str:
        return self.text[self.open_brace + 1:self.end]


def _strip_comments(text: str) -> str:
    return re.sub(r"#[^\n]*", lambda m: " " * len(m.group(0)), text)


def server_blocks(path: str, text: str) -> List[ServerBlock]:
    clean = _strip_comments(text)
    blocks = []
    for match in re.finditer(r"\bserver\s*\{", clean):
        depth, i = 0, match.end() - 1
        while i < len(clean):
            if clean[i] == "{":
                depth += 1
            elif clean[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = clean[match.end():i]
        # Only top-level server blocks of http{} (not upstream "server host:port;").
        tls = bool(re.search(r"\blisten\s+[^;]*\b(443|ssl)\b", body))
        blocks.append(ServerBlock(path, text, match.start(), match.end() - 1, i, tls))
    return blocks


def _names(block: ServerBlock) -> List[str]:
    names: List[str] = []
    for match in re.finditer(r"\bserver_name\s+([^;]+);", _strip_comments(block.body)):
        names.extend(match.group(1).split())
    return names


def _matches(name: str, host: str) -> bool:
    name, host = name.lower().rstrip("."), host.lower()
    if name == host:
        return True
    if name.startswith("*.") and host.endswith(name[1:]):
        return True
    return name.startswith(".") and (host == name[1:] or host.endswith(name))


def find_server_block(host: str, *, read: Callable[[str], str],
                      paths: Optional[Sequence[str]] = None) -> Optional[ServerBlock]:
    """The HTTPS server block serving *host* (an http-only one when there is none)."""
    if paths is None:
        paths = [p for pattern in NGINX_CONFIG_GLOBS for p in sorted(glob.glob(pattern))]
    plain = None
    for path in paths:
        try:
            text = read(path)
        except OSError:
            continue
        for block in server_blocks(path, text):
            if any(_matches(n, host) for n in _names(block)):
                if block.tls:
                    return block
                plain = plain or block
    return plain


def include_line(host: str) -> str:
    return f"    include {INCLUDE_DIR}/{host}.conf;"


def with_include(block: ServerBlock, host: str) -> Optional[str]:
    """The file text with the include line added to *block* (None when present already)."""
    line = include_line(host)
    if line.strip() in block.body:
        return None
    return block.text[:block.open_brace + 1] + "\n" + line + block.text[block.open_brace + 1:]


def conflicts(block: ServerBlock, *, route_path: Optional[str], articles_path: Optional[str]) -> List[str]:
    """Locations the site already defines for the connector's paths."""
    paths = [route_path] if route_path else []
    if articles_path:
        articles = "/" + articles_path.strip("/")
        paths += [articles, articles + "/"]
    body = _strip_comments(block.body)
    found = []
    for path in paths:
        if re.search(r"\blocation\s+(=|\^~)?\s*" + re.escape(path) + r"\s*\{", body):
            found.append(path)
    return found
