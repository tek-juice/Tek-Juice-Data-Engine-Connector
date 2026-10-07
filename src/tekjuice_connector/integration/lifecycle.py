"""Framework-neutral application lifecycle helpers.

Provides :func:`connect_on_startup` and :func:`disconnect_on_shutdown` —
plain functions that can be wired into any application startup/shutdown
mechanism without framework coupling.

These are intentionally simple.  They do not manage threads, async loops,
background tasks, or process signals.  The customer backend decides when
and how to call them.

Example — synchronous startup::

    from tekjuice_connector.integration.lifecycle import connect_on_startup
    from tekjuice_connector import TekJuiceConnector

    connector = TekJuiceConnector()
    result = connect_on_startup(connector)
    if not result.ready:
        raise RuntimeError("Connector not READY — cannot start application.")

Example — with hooks::

    from tekjuice_connector.integration.hooks import ConnectorHooks
    from tekjuice_connector.integration.lifecycle import connect_on_startup

    hooks = ConnectorHooks()

    @hooks.on_ready
    def _on_ready(result):
        print("READY:", result.domain)

    result = connect_on_startup(connector, hooks=hooks)
"""

from __future__ import annotations

import logging
from typing import Optional

from tekjuice_connector.integration.hooks import ConnectorHooks
from tekjuice_connector.models.connection import ConnectionResult

_log = logging.getLogger(__name__)


def connect_on_startup(
    connector: object,
    *,
    hooks: Optional[ConnectorHooks] = None,
    raise_on_not_ready: bool = False,
) -> ConnectionResult:
    """Connect the Connector at application startup.

    Parameters
    ----------
    connector:
        A ``TekJuiceConnector`` instance.
    hooks:
        Optional :class:`ConnectorHooks` instance.  When provided, the
        appropriate hooks are fired after the connection attempt.
    raise_on_not_ready:
        When ``True``, raise :class:`RuntimeError` if the Connector is not
        READY after connecting.  Useful for applications that cannot start
        without a healthy connection.

    Returns
    -------
    ConnectionResult
    """
    _log.info("Connecting to Tek Juice Data Engine on application startup …")
    result: ConnectionResult = connector.connect()  # type: ignore[union-attr]

    if result.ready:
        _log.info("Connector READY (domain=%s).", result.domain)
    else:
        _log.warning(
            "Connector not READY after startup. "
            "State: %s | Onboarding: %s",
            result.state.value,
            result.onboarding_status,
        )

    if hooks is not None:
        hooks.fire_on_connect(result)

    if raise_on_not_ready and not result.ready:
        raise RuntimeError(
            f"Tek Juice Connector is not READY after startup. "
            f"State: {result.state.value}, "
            f"Onboarding: {result.onboarding_status}. "
            f"Check configuration and Data Engine status."
        )

    return result


def disconnect_on_shutdown(connector: object) -> None:
    """Perform any cleanup needed when the application shuts down.

    The Connector is stateless — it holds no persistent server-side session
    and no open socket.  This function exists as a clean hook for future
    use (e.g. flushing metrics, closing a custom session) and to signal
    intent in application lifecycle code.

    Parameters
    ----------
    connector:
        A ``TekJuiceConnector`` instance.
    """
    _log.info("Tek Juice Connector shutting down.")
    # Currently a no-op; the Connector holds no resources requiring explicit release.
    # Reserved for future cleanup (e.g. draining a metrics buffer).
