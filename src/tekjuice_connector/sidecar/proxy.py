"""Reverse-proxy configuration for the sidecar (``tekjuice proxy-config``).

Generated from the sidecar's own configuration, so the routes match what it
serves:

* the API host (``TEKJUICE_API_BASE_URL``): the S2S receiver route, with the
  ``Authorization`` header passed through;
* the website host (``TEKJUICE_SITE_URL``, when discoverability is on): the
  sitemap, robots.txt, llms.txt, feed, ``/<slug>.md``, the IndexNow key file,
  pre-rendered article pages (from ``TEKJUICE_PUBLIC_URL_TEMPLATE``) and the
  static pages that may carry approved site changes. Every one of these falls
  back to the site itself when the sidecar answers 404, so nothing the site
  already serves is hidden.

The output is a starting point you review and install; the connector never
edits a running proxy.
"""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlsplit

from tekjuice_connector.sidecar.server import SidecarConfig

SITE_WIDE = ("sitemap.xml", "robots.txt", "llms.txt", "llms-full.txt", "feed.json")


def _article_prefix(template: Optional[str]) -> Optional[str]:
    """``/blog/`` for ``https://www.example.com/blog/{slug}``; ``/`` at the root."""
    if not template or "{slug}" not in template:
        return None
    path = urlsplit(template.replace("{slug}", "SLUG")).path
    return path[: path.index("SLUG")] if "SLUG" in path else None


def _hosts(config: SidecarConfig) -> tuple:
    api = urlsplit(config.api_base_url or "").hostname
    site = urlsplit(config.site_url or "").hostname
    return api, site


def _site_matchers(config: SidecarConfig, template: Optional[str]) -> tuple:
    """(exact paths, prefix paths, regexes) that may be served by the sidecar."""
    exact = ["/" + name for name in SITE_WIDE] + [p for p in config.static_paths]
    if config.hub_path:
        exact.append("/" + config.hub_path.strip("/"))
    prefixes, regexes = [], [r"^/[0-9a-f]{32}\.txt$", r"^/[a-z0-9-]+\.md$"]
    prefix = _article_prefix(template or config.public_url_template)
    if prefix and prefix != "/":
        prefixes.append(prefix)
    elif prefix == "/":
        regexes.append(r"^/[a-z0-9-]+$")
    return list(dict.fromkeys(exact)), prefixes, regexes


def nginx(config: SidecarConfig, *, upstream: str = "http://127.0.0.1:8787",
          site_upstream: str = "http://127.0.0.1:3000", template: Optional[str] = None,
          listen: str = "443", certificate: Optional[tuple] = None) -> str:
    upstream = upstream.rstrip("/")
    seo = config.seo_prefix.rstrip("/")
    api_host, site_host = _hosts(config)
    api_path = urlsplit(config.api_base_url or "").path.rstrip("/") + config.route_path
    receiver = f"""    # Tek Juice S2S receiver (the Data Engine publishes here)
    location = {api_path} {{
        rewrite ^ {config.route_path} break;
        proxy_pass {upstream};
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Host $host;
        client_max_body_size 3m;
        proxy_read_timeout 60s;
    }}
"""
    seo_locations = ""
    if config.site_url:
        exact, prefixes, regexes = _site_matchers(config, template)
        via = (f"        rewrite ^ {seo}/site$uri break;\n        proxy_pass {upstream};\n"
               "        proxy_intercept_errors on;\n        error_page 404 = @tekjuice_site;\n")
        lines = ["    # Tek Juice discoverability: the sidecar answers, or 404 falls back to the site",
                 "    location @tekjuice_site {", f"        proxy_pass {site_upstream.rstrip('/')};",
                 "        proxy_set_header Host $host;", "    }"]
        lines += [f"    location = {path} {{\n{via}    }}" for path in exact]
        lines += [f"    location ^~ {prefix} {{\n{via}    }}" for prefix in prefixes]
        # Quoted: nginx would read "{32}" as a block.
        lines += [f'    location ~ "{regex}" {{\n{via}    }}' for regex in regexes]
        lines += ["    location / {", f"        proxy_pass {site_upstream.rstrip('/')};",
                  "        proxy_set_header Host $host;", "    }"]
        seo_locations = "\n".join(lines) + "\n"
    blocks: List[str] = []
    if site_host and site_host == api_host:
        blocks.append(_nginx_server(site_host, receiver + seo_locations, listen, certificate))
    else:
        blocks.append(_nginx_server(api_host or "api.example.com", receiver, listen, certificate))
        if seo_locations:
            blocks.append(_nginx_server(site_host, seo_locations, listen, certificate))
    return "\n".join(blocks)


def _nginx_server(host: str, body: str, listen: str = "443", certificate: Optional[tuple] = None) -> str:
    tls = (f"    ssl_certificate {certificate[0]};\n    ssl_certificate_key {certificate[1]};\n" if certificate
           else "    # ssl_certificate / ssl_certificate_key: your certificates (e.g. certbot)\n")
    return f"""server {{
    listen {listen} ssl;
    http2 on;
    server_name {host};
{tls}
{body}}}
"""


def caddy(config: SidecarConfig, *, upstream: str = "127.0.0.1:8787", site_upstream: str = "127.0.0.1:3000",
          template: Optional[str] = None, tls_internal: bool = False) -> str:
    """A Caddyfile; Caddy obtains and renews certificates automatically."""
    upstream = upstream.replace("http://", "").rstrip("/")
    site_upstream = site_upstream.replace("http://", "").rstrip("/")
    seo = config.seo_prefix.rstrip("/")
    api_host, site_host = _hosts(config)
    api_path = urlsplit(config.api_base_url or "").path.rstrip("/") + config.route_path
    tls = "    tls internal\n" if tls_internal else ""
    receiver = f"""    handle {api_path} {{
        reverse_proxy {upstream} {{
            rewrite {config.route_path}
        }}
    }}
"""
    site = ""
    if config.site_url:
        exact, prefixes, regexes = _site_matchers(config, template)
        paths = " ".join(exact + [p + "*" for p in prefixes])
        matcher = "    @tekjuice {\n" + f"        path {paths}\n    }}\n"
        for i, regex in enumerate(regexes):
            matcher += f"    @tekjuice_re{i} path_regexp {regex}\n"
        names = ["@tekjuice"] + [f"@tekjuice_re{i}" for i in range(len(regexes))]
        block = "".join(f"""    handle {name} {{
        reverse_proxy {upstream} {{
            rewrite {seo}/site{{path}}
            @missing status 404
            handle_response @missing {{
                reverse_proxy {site_upstream}
            }}
        }}
    }}
""" for name in names)
        site = matcher + block + f"    handle {{\n        reverse_proxy {site_upstream}\n    }}\n"
    if site_host and site_host == api_host:
        return f"{site_host} {{\n{tls}{receiver}{site}}}\n"
    out = f"{api_host or 'api.example.com'} {{\n{tls}{receiver}}}\n"
    if site:
        out += f"\n{site_host} {{\n{tls}{site}}}\n"
    return out
