"""Integration context — framework-neutral shared state.

``ConnectorContext`` is an optional convenience object that holds an
initialised :class:`~tekjuice_connector.TekJuiceConnector` instance and
the most recent :class:`~tekjuice_connector.models.ConnectionResult`.

It is **not** tied to any framework.  A customer backend can store it
however their application manages shared objects — a module-level
singleton, a dependency-injection container, a class attribute, or
anything else.

Example::

    from tekjuice_connector.integration.context import ConnectorContext

    ctx = ConnectorContext.initialise()   # loads env vars, connects
    if ctx.ready:
        print("Connected:", ctx.result.domain)

    # Later:
    ctx.reconnect()   # safe to call again; idempotent
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tekjuice_connector.models.connection import ConnectionResult, ConnectionState


@dataclass
class ConnectorContext:
    """Holds a live connector and its most recent connection result.

    Attributes
    ----------
    connector:
        The underlying ``TekJuiceConnector`` instance.
    result:
        Most recent :class:`ConnectionResult`, or ``None`` before the first
        successful ``connect()`` call.
    """

    connector: object  # TekJuiceConnector — typed as object to avoid circular import
    result: Optional[ConnectionResult] = field(default=None)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """``True`` when the last connection result is READY."""
        return self.result is not None and self.result.ready

    @property
    def state(self) -> ConnectionState:
        """Current state from the last result, or DISCONNECTED."""
        if self.result is not None:
            return self.result.state
        return ConnectionState.DISCONNECTED

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def reconnect(self) -> ConnectionResult:
        """Re-run ``connect()`` and update the stored result.

        Safe to call repeatedly — idempotent for READY websites.
        """
        result: ConnectionResult = self.connector.connect()  # type: ignore[union-attr]
        self.result = result
        return result

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def initialise(
        cls,
        *,
        website_id: Optional[str] = None,
        connector_key: Optional[str] = None,
        data_engine_url: Optional[str] = None,
    ) -> "ConnectorContext":
        """Create a :class:`ConnectorContext`, connect, and return it.

        Parameters
        ----------
        website_id, connector_key, data_engine_url:
            Explicit overrides; environment variables used when absent.

        Returns
        -------
        ConnectorContext
            The context with ``result`` populated from the first
            ``connect()`` call.

        Raises
        ------
        TekJuiceConnectorError
            Any configuration or connection error from the initial connect.
        """
        # Import here to break the circular reference between this module
        # and the top-level package __init__.
        from tekjuice_connector import TekJuiceConnector  # noqa: PLC0415

        connector = TekJuiceConnector(
            website_id=website_id,
            connector_key=connector_key,
            data_engine_url=data_engine_url,
        )
        result = connector.connect()
        return cls(connector=connector, result=result)

    def __repr__(self) -> str:
        return (
            f"ConnectorContext(ready={self.ready}, state={self.state.value!r})"
        )
