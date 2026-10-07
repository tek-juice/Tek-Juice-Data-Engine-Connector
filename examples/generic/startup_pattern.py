"""Generic example — framework-neutral application startup pattern.

This illustrates how any backend application (regardless of framework) can
wire the Connector into its startup and shutdown lifecycle using the
generic helpers.

The Connector itself has no dependency on the application framework.
The customer decides how to store and share the connector instance.
"""

from __future__ import annotations

import sys

from tekjuice_connector import TekJuiceConnector, TekJuiceConnectorError
from tekjuice_connector.integration.lifecycle import (
    connect_on_startup,
    disconnect_on_shutdown,
)
from tekjuice_connector.integration.hooks import ConnectorHooks

# ---------------------------------------------------------------------------
# Module-level singleton — simplest sharing strategy
# ---------------------------------------------------------------------------

_connector: TekJuiceConnector | None = None


def get_connector() -> TekJuiceConnector:
    """Return the initialised connector instance.

    Raises RuntimeError if startup() has not been called.
    """
    if _connector is None:
        raise RuntimeError(
            "Connector not initialised. Call startup() before use."
        )
    return _connector


# ---------------------------------------------------------------------------
# Hooks (optional — remove if not needed)
# ---------------------------------------------------------------------------

hooks = ConnectorHooks()


@hooks.on_ready
def _on_ready(result):
    print(f"[Connector] READY — domain={result.domain}, tenant={result.tenant_id}")


@hooks.on_failed
def _on_failed(result):
    print(f"[Connector] FAILED — {result.message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

def startup(raise_on_not_ready: bool = True) -> None:
    """Connect the Tek Juice Connector.

    Call this once during application startup — before handling any requests.

    Parameters
    ----------
    raise_on_not_ready:
        When True (default) raises RuntimeError if the Connector cannot
        reach READY state.  Set False to allow degraded startup.
    """
    global _connector
    _connector = TekJuiceConnector()
    connect_on_startup(_connector, hooks=hooks, raise_on_not_ready=raise_on_not_ready)


def shutdown() -> None:
    """Disconnect the Tek Juice Connector.

    Call this during application shutdown.
    """
    global _connector
    if _connector is not None:
        disconnect_on_shutdown(_connector)
        _connector = None


# ---------------------------------------------------------------------------
# Example usage (standalone script)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting up …")
    try:
        startup(raise_on_not_ready=False)
    except TekJuiceConnectorError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        sys.exit(1)

    connector = get_connector()
    result = connector.status()
    print(f"Status: {result.state.value} | Ready: {result.ready}")

    print("Shutting down …")
    shutdown()
