"""Framework-neutral adapter protocol.

Defines the :class:`ConnectorAdapter` Protocol that framework-specific
shims should implement.  This keeps the Connector boundary clean: the
package itself never imports Django, FastAPI, Flask, or any other framework.

A customer who wants deep framework integration can implement this protocol
in their own codebase (or in a separate adapter package) without modifying
the Connector.

Example — a minimal custom adapter::

    from tekjuice_connector.integration.adapter import ConnectorAdapter
    from tekjuice_connector import TekJuiceConnector
    from tekjuice_connector.models import ConnectionResult

    class MyAppConnectorAdapter:
        \"\"\"Integrates TekJuiceConnector with our application.\"\"\"

        def __init__(self) -> None:
            self._connector = TekJuiceConnector()

        def initialise(self) -> ConnectionResult:
            return self._connector.connect()

        def get_connector(self) -> TekJuiceConnector:
            return self._connector

        def is_ready(self) -> bool:
            return self._connector.status().ready

        def shutdown(self) -> None:
            pass  # Connector is stateless; nothing to tear down.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tekjuice_connector.models.connection import ConnectionResult


@runtime_checkable
class ConnectorAdapter(Protocol):
    """Protocol for framework-specific connector adapters.

    Implementers should provide:

    - ``initialise()`` — called once at application startup to establish
      the connection.
    - ``get_connector()`` — returns the underlying connector instance for
      direct use.
    - ``is_ready()`` — quick non-network check of current ready state.
    - ``shutdown()`` — called at application shutdown for cleanup.
    """

    def initialise(self) -> ConnectionResult:
        """Connect to the Data Engine and return the result."""
        ...

    def get_connector(self) -> object:
        """Return the underlying ``TekJuiceConnector`` instance."""
        ...

    def is_ready(self) -> bool:
        """Return ``True`` if the last known state is READY."""
        ...

    def shutdown(self) -> None:
        """Release any resources held by the adapter."""
        ...
