"""Framework-neutral lifecycle hooks.

Provides a simple callable-based hook system that a customer backend can
use to react to Connector lifecycle events, regardless of whether they are
using Django signals, FastAPI lifespan events, WSGI middleware, or
something else entirely.

The hooks are purely additive — the Connector works correctly without any
hooks registered.  They are a convenience for customers who want to:

- Warm up a connection pool when the application starts.
- Log connector state changes.
- Trigger alerts when the Connector enters a FAILED state.
- Gate request handling on READY status.

No framework is imported or required.

Example::

    from tekjuice_connector.integration.hooks import ConnectorHooks

    hooks = ConnectorHooks()

    @hooks.on_ready
    def handle_ready(result):
        print(f"Connector READY for domain: {result.domain}")

    @hooks.on_failed
    def handle_failed(result):
        alert_team(f"Connector FAILED: {result.message}")

    # Trigger at app startup:
    hooks.fire_on_connect(connector.connect())
"""

from __future__ import annotations

from typing import Callable, List, Optional

from tekjuice_connector.models.connection import ConnectionResult, ConnectionState

# Type alias for a hook callback.
ConnectorHookFn = Callable[[ConnectionResult], None]


class ConnectorHooks:
    """Registry of user-supplied lifecycle hook callbacks.

    All callbacks receive the :class:`ConnectionResult` as their only
    argument.  Exceptions raised inside a callback are caught and logged
    so that one bad callback cannot prevent other hooks or the main
    application from running.
    """

    def __init__(self) -> None:
        self._on_ready: List[ConnectorHookFn] = []
        self._on_connected: List[ConnectorHookFn] = []
        self._on_failed: List[ConnectorHookFn] = []
        self._on_any: List[ConnectorHookFn] = []

    # ------------------------------------------------------------------
    # Registration decorators / methods
    # ------------------------------------------------------------------

    def on_ready(self, fn: ConnectorHookFn) -> ConnectorHookFn:
        """Register *fn* to be called when the Connector reaches READY state."""
        self._on_ready.append(fn)
        return fn

    def on_connected(self, fn: ConnectorHookFn) -> ConnectorHookFn:
        """Register *fn* to be called on any successful connection (including
        non-READY authenticated states)."""
        self._on_connected.append(fn)
        return fn

    def on_failed(self, fn: ConnectorHookFn) -> ConnectorHookFn:
        """Register *fn* to be called when the connection enters FAILED state."""
        self._on_failed.append(fn)
        return fn

    def on_any(self, fn: ConnectorHookFn) -> ConnectorHookFn:
        """Register *fn* to be called on every connection result."""
        self._on_any.append(fn)
        return fn

    # ------------------------------------------------------------------
    # Firing
    # ------------------------------------------------------------------

    def fire_on_connect(self, result: ConnectionResult) -> None:
        """Dispatch the appropriate hooks for *result*.

        Calls ``on_any`` hooks first, then state-specific hooks.
        Exceptions from individual hooks are caught and logged without
        re-raising so that a failing hook does not break the application.
        """
        import logging
        log = logging.getLogger(__name__)

        self._fire(self._on_any, result, log)

        if result.state is ConnectionState.READY:
            self._fire(self._on_ready, result, log)
            self._fire(self._on_connected, result, log)
        elif result.state is ConnectionState.FAILED:
            self._fire(self._on_failed, result, log)
        else:
            self._fire(self._on_connected, result, log)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fire(
        callbacks: List[ConnectorHookFn],
        result: ConnectionResult,
        log: object,
    ) -> None:
        import logging
        _log = log if isinstance(log, logging.Logger) else logging.getLogger(__name__)
        for cb in callbacks:
            try:
                cb(result)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "ConnectorHook callback %r raised an exception: %s",
                    getattr(cb, "__name__", repr(cb)),
                    exc,
                )

    def __repr__(self) -> str:
        return (
            f"ConnectorHooks("
            f"on_ready={len(self._on_ready)}, "
            f"on_connected={len(self._on_connected)}, "
            f"on_failed={len(self._on_failed)}, "
            f"on_any={len(self._on_any)})"
        )
