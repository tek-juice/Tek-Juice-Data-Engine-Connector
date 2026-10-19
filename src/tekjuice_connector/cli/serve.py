"""CLI: ``tekjuice serve`` — run the connector sidecar.

Configured through environment variables (docs/sidecar.md). Exit code 2 when
the configuration is invalid; otherwise runs until stopped.
"""

from __future__ import annotations

import logging
import sys


def run_serve() -> int:
    from tekjuice_connector.logging.logger import configure_logging
    from tekjuice_connector.sidecar import Sidecar, SidecarConfig

    configure_logging()
    logging.getLogger("tekjuice_connector").setLevel(logging.INFO)
    try:
        config = SidecarConfig.from_env()
        sidecar = Sidecar(config)
    except (ValueError, OSError) as exc:
        print(f"Configuration invalid: {exc}", file=sys.stderr)
        return 2
    print(f"tekjuice-connector sidecar listening on {config.host}:{config.port}, "
          f"route {config.route_path}", file=sys.stderr)
    sidecar.serve_forever()
    return 0
