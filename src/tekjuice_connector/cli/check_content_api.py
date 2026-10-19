"""CLI: ``tekjuice check-content-api`` — conformance checks for a content API.

Non-mutating checks of the backend's content API (any language), with the
sidecar's own configuration (``TEKJUICE_CONTENT_API_URL``,
``TEKJUICE_CONTENT_API_TOKEN[_FILE]``, ``TEKJUICE_PUBLIC_URL_TEMPLATE``):

1. ``GET /capabilities`` (optional) and a known public URL template;
2. the API rejects requests without the token (when a token is configured);
3. ``GET /content/{random slug}`` answers 404 with the token;
4. ``POST /content/validate`` (when advertised) accepts a valid sample and
   rejects an invalid one.

Nothing is created or changed. Exit code 0 when every check passes.
"""

from __future__ import annotations

import os
import secrets
import sys
from typing import List, Tuple


def run_check_content_api() -> int:
    from tekjuice_connector.publication.contract import PublicationContent
    from tekjuice_connector.sidecar.content_api import HttpContentStore, _requests_call
    from tekjuice_connector.sidecar.server import SidecarConfig

    env = dict(os.environ)
    env.setdefault("TEKJUICE_WEBSITE_ID", "check")
    env.setdefault("TEKJUICE_CONNECTOR_KEY", "check")
    env.setdefault("TEKJUICE_DATA_ENGINE_URL", "https://engine.invalid")
    results: List[Tuple[bool, str]] = []
    try:
        config = SidecarConfig.from_env(env)
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = HttpContentStore(config.content_api_url, ledger_path=f"{tmp}/ledger.sqlite3",
                                     token=config.content_api_token,
                                     public_url_template=config.public_url_template)
            results.append((True, f"public URL template: {store.public_url('{slug}')}"))
            results.append((True, "update supported" if callable(getattr(store, "update", None))
                            else "update not supported (create only)"))
            base = config.content_api_url.rstrip("/")
            slug = f"tekjuice-check-{secrets.token_hex(4)}"
            if config.content_api_token:
                status, _ = _requests_call("GET", f"{base}/content/{slug}", {"Accept": "application/json"},
                                           None, 10)
                results.append((status in (401, 403), f"request without the token is rejected (HTTP {status})"))
            status, _ = store._call("GET", f"/content/{slug}")  # noqa: SLF001
            results.append((status == 404, f"unknown slug returns 404 (HTTP {status})"))
            if store._remote_validate:  # noqa: SLF001
                good = PublicationContent(title="Check", body="<p>Check</p>", slug=slug)
                store.validate(good)
                results.append((True, "validate accepts valid content"))
                try:
                    store.validate(PublicationContent(title="", body="", slug=slug))
                    results.append((False, "validate accepted empty content"))
                except Exception:  # noqa: BLE001
                    results.append((True, "validate rejects invalid content"))
    except Exception as exc:  # noqa: BLE001
        results.append((False, f"{type(exc).__name__}: {exc}"))
    for ok, message in results:
        print(f"[{'pass' if ok else 'FAIL'}] {message}")
    return 0 if all(ok for ok, _ in results) else 1
