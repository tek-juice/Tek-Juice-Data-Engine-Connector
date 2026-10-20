"""CLI: ``tekjuice content-types`` — the backend's content types as the connector sees them.

Reads the sidecar's own configuration (``TEKJUICE_CONTENT_API_URL``,
``TEKJUICE_OPENAPI_PATH``, ``TEKJUICE_WORDPRESS_*``, ``TEKJUICE_CONTENT_TYPES``)
and prints each discovered content type with its status (discovered,
validated, authorized, ready, unsupported), operations, required fields and
what is missing. ``--json`` prints the capability map as the receiver
reports it to the Data Engine. Read-only: nothing is created or changed, and
no credential or backend address is printed. Exit code 0 when at least one
content type is ready.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict


def render(described: Dict[str, Any]) -> str:
    lines = [f"default content type: {described.get('default') or 'none'}"]
    for t in described.get("types") or []:
        lines.append(f"\n{t['name']} ({t['label']}) — {t['status'].upper()}")
        lines.append(f"  adapter: {t['adapter']}; category: {t.get('category') or 'unclassified'}; "
                     f"identity: {t['identity']}; public page: {'yes' if t['public'] else 'no'}")
        lines.append(f"  operations: {', '.join(t['operations']) or 'none'} "
                     f"(Tek Juice performs: {', '.join(t['executable_operations']) or 'none'})")
        if t.get("required_fields"):
            lines.append(f"  required fields: {', '.join(t['required_fields'])}")
        if t.get("public_url_template"):
            lines.append(f"  public URL: {t['public_url_template']}")
        for item in t.get("evidence") or []:
            lines.append(f"  evidence: {item}")
        for item in t.get("limitations") or []:
            lines.append(f"  ! {item}")
    for error in described.get("errors") or []:
        lines.append(f"\nsource error: {error}")
    if not described.get("types"):
        lines.append("\nno content types found")
    return "\n".join(lines)


def run_content_types(as_json: bool = False) -> int:
    from tekjuice_connector.publication import InMemoryContentStore
    from tekjuice_connector.sidecar.server import Sidecar, SidecarConfig

    env = dict(os.environ)
    env.setdefault("TEKJUICE_WEBSITE_ID", "check")
    env.setdefault("TEKJUICE_CONNECTOR_KEY", "check")
    env.setdefault("TEKJUICE_DATA_ENGINE_URL", "https://engine.invalid")
    with tempfile.TemporaryDirectory() as tmp:
        env["TEKJUICE_DATA_DIR"] = tmp
        try:
            config = SidecarConfig.from_env(env)
        except ValueError as exc:
            print(f"configuration error: {exc}", file=sys.stderr)
            return 2
        # The default type only needs to be described; nothing is published.
        store = InMemoryContentStore(config.public_url_template.split("{slug}")[0]
                                     if config.public_url_template else "https://example.invalid")
        described = Sidecar._content_types(config, store).describe()  # noqa: SLF001
    print(json.dumps(described, indent=2) if as_json else render(described))
    return 0 if described.get("ready") else 1
