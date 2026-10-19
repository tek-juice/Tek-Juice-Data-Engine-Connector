"""CLI: ``tekjuice proxy-config`` — print reverse-proxy configuration.

Generated from the sidecar's environment configuration (receiver route on
the API host; discoverability routes on the website host, each falling back
to the site). Review it, then install it in your proxy; nothing is changed
automatically. ``--caddy`` gives a Caddyfile (automatic HTTPS), ``--nginx``
server blocks.
"""

from __future__ import annotations

import os
import sys


def run_proxy_config(*, flavour: str, upstream: str, site_upstream: str, template: str | None = None) -> int:
    from tekjuice_connector.sidecar import proxy
    from tekjuice_connector.sidecar.server import SidecarConfig

    env = dict(os.environ)
    for key, value in (("TEKJUICE_CONNECTOR_KEY", "unused"), ("TEKJUICE_DATA_ENGINE_URL", "https://unused"),
                       ("TEKJUICE_WEBSITE_ID", "unused"), ("TEKJUICE_CONTENT_API_URL", "http://unused")):
        env.setdefault(key, value)
    try:
        config = SidecarConfig.from_env(env)
    except (ValueError, OSError) as exc:
        print(f"Configuration invalid: {exc}", file=sys.stderr)
        return 2
    if not config.api_base_url:
        print("Set TEKJUICE_API_BASE_URL (the public host the Data Engine calls).", file=sys.stderr)
        return 2
    if flavour == "caddy":
        print(proxy.caddy(config, upstream=upstream, site_upstream=site_upstream, template=template))
    else:
        print(proxy.nginx(config, upstream=upstream, site_upstream=site_upstream, template=template))
    return 0
