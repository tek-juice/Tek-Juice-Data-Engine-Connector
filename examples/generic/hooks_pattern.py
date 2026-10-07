"""Generic example — lifecycle hooks pattern.

Demonstrates how to use ConnectorHooks to react to connection state changes.
Works in any Python application regardless of framework.
"""

from __future__ import annotations

from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.integration.hooks import ConnectorHooks
from tekjuice_connector.models.connection import ConnectionResult


def build_hooks() -> ConnectorHooks:
    hooks = ConnectorHooks()

    @hooks.on_ready
    def handle_ready(result: ConnectionResult) -> None:
        """Called when the Connector reaches READY state."""
        print(f"Connector READY — domain: {result.domain}")

    @hooks.on_connected
    def handle_connected(result: ConnectionResult) -> None:
        """Called on any successful authentication (READY or not)."""
        print(f"Connector authenticated — status: {result.onboarding_status}")

    @hooks.on_failed
    def handle_failed(result: ConnectionResult) -> None:
        """Called when the connection fails."""
        print(f"Connector FAILED — {result.message}")
        # In a real application: send an alert, update a health endpoint, etc.

    @hooks.on_any
    def log_every(result: ConnectionResult) -> None:
        """Called on every connect() result, regardless of state."""
        print(f"[hook:on_any] state={result.state.value} ready={result.ready}")

    return hooks


if __name__ == "__main__":
    import sys

    hooks = build_hooks()

    try:
        connector = TekJuiceConnector()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        result = connector.connect()
        hooks.fire_on_connect(result)
    except Exception as exc:
        failed_result = __import__(
            "tekjuice_connector.models.connection", fromlist=["ConnectionResult"]
        ).ConnectionResult.failed(str(exc))
        hooks.fire_on_connect(failed_result)
        sys.exit(1)
