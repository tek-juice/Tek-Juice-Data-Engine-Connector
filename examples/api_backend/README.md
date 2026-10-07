# API backend integration example

This directory is reserved for illustrative API backend wiring examples.

> **Important:** The Connector itself is completely framework-independent.
> These examples show *where* to place the `connect()` call, not a
> production-ready integration.

## Health endpoint pattern

A common use case is exposing a health endpoint that includes Connector state:

```python
# health.py — framework-agnostic example
from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.connection.health import check_health

connector = TekJuiceConnector()

def get_health() -> dict:
    """Return a health dict suitable for any HTTP health endpoint."""
    health = check_health(connector._manager)
    return {
        "status": "healthy" if health.healthy else "unhealthy",
        "connector_state": health.state.value,
        "ready": health.healthy,
    }
```

`check_health()` never raises — it returns a `HealthResult` with
`healthy=False` on any error. This is safe to call from a health endpoint
without try/except.

## Gating requests on READY state

```python
# In any request handler, after startup connect():
result = connector.status()  # no network call

if not result.ready:
    # Return 503 Service Unavailable or similar
    raise ServiceUnavailableError("Tek Juice Connector not READY")
```

`status()` returns the last known state without making a network request,
so it is safe to call on every request.

## Reconnect pattern

If your application detects that the Connector is no longer READY
(e.g. after a network partition), trigger a reconnect:

```python
result = connector.status()

if not result.ready:
    result = connector.connect()  # performs a fresh handshake with retries
```

`connect()` is idempotent — safe to call repeatedly.
