# Integration

## Framework-neutral design

The Tek Juice Connector is a plain Python package. It has no dependency on
Django, FastAPI, Flask, Celery, or any other framework. You wire it into
your application using whichever startup/shutdown mechanism your framework
provides.

## Generic lifecycle helpers

The simplest integration pattern uses two plain functions:

```python
from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.integration.lifecycle import (
    connect_on_startup,
    disconnect_on_shutdown,
)

connector = TekJuiceConnector()

# --- Application startup ---
result = connect_on_startup(connector, raise_on_not_ready=True)

# --- Application shutdown ---
disconnect_on_shutdown(connector)
```

These functions are framework-agnostic. Call them from Django `AppConfig.ready()`,
a FastAPI `lifespan` handler, a Flask `before_first_request`, or a plain
`__main__` block — the Connector does not care.

## ConnectorContext

`ConnectorContext` is a convenience wrapper for applications that want to
share the connector and its current result as a single object:

```python
from tekjuice_connector.integration.context import ConnectorContext

# Initialise and connect in one call
ctx = ConnectorContext.initialise()

if ctx.ready:
    print("Domain:", ctx.result.domain)

# Later — reconnect safely (idempotent for READY websites)
ctx.reconnect()
```

Store `ctx` however your application shares objects — a module-level
singleton, a class attribute, a dependency injection container, etc.

## Lifecycle hooks

Register callbacks that react to connection state changes without polling:

```python
from tekjuice_connector.integration.hooks import ConnectorHooks
from tekjuice_connector.integration.lifecycle import connect_on_startup
from tekjuice_connector import TekJuiceConnector

hooks = ConnectorHooks()

@hooks.on_ready
def on_ready(result):
    print("Connector READY — domain:", result.domain)

@hooks.on_failed
def on_failed(result):
    alert_ops_team(f"Connector FAILED: {result.message}")

connector = TekJuiceConnector()
connect_on_startup(connector, hooks=hooks)
```

Available hook decorators:
- `@hooks.on_ready` — fired when state is `READY`
- `@hooks.on_connected` — fired on any successful connection
- `@hooks.on_failed` — fired when state is `FAILED`
- `@hooks.on_any` — fired on every `connect()` result

Exceptions raised inside a hook callback are caught and logged; they will
not prevent other hooks or your application from running.

## Implementing a custom adapter

If you want to encapsulate the Connector behind a clean interface in your
application, implement the `ConnectorAdapter` protocol:

```python
from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.integration.adapter import ConnectorAdapter
from tekjuice_connector.models.connection import ConnectionResult

class MyAppConnector:
    def __init__(self) -> None:
        self._connector = TekJuiceConnector()
        self._result: ConnectionResult | None = None

    def initialise(self) -> ConnectionResult:
        self._result = self._connector.connect()
        return self._result

    def get_connector(self) -> TekJuiceConnector:
        return self._connector

    def is_ready(self) -> bool:
        return self._result is not None and self._result.ready

    def shutdown(self) -> None:
        pass  # Connector is stateless
```

The `ConnectorAdapter` protocol is `runtime_checkable`:

```python
assert isinstance(MyAppConnector(), ConnectorAdapter)
```

## What the Connector does NOT integrate with

The Connector does not and must not:

- Access the Data Engine database directly.
- Import Data Engine internal modules.
- Implement M2M execution or authentication.
- Implement content generation, SEO, or publication.
- Manage Django ORM models.
- Manage FastAPI dependency injection trees.
- Run Celery tasks.

Framework-specific wiring is always the responsibility of the customer
backend, not the Connector.
