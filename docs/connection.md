# Connection

## Connection states

The Connector maintains a local state machine that reflects what it has
observed from the Data Engine. The Data Engine remains authoritative for
actual onboarding state.

| State | Meaning |
|---|---|
| `DISCONNECTED` | No connection attempt has been made yet |
| `CONNECTING` | A handshake is in progress |
| `AUTHENTICATED` | Connector key accepted; backend connection pending |
| `BACKEND_CONNECTED` | Backend is connected; full READY pending |
| `READY` | Fully authenticated and connected — safe to use |
| `FAILED` | Last connection attempt failed |

## Basic usage

```python
from tekjuice_connector import TekJuiceConnector

connector = TekJuiceConnector()
result = connector.connect()

if result.ready:
    print("Connected to domain:", result.domain)
```

## ConnectionResult fields

| Field | Type | Description |
|---|---|---|
| `ready` | `bool` | `True` when the Data Engine reported READY |
| `state` | `ConnectionState` | Connector-side state |
| `website_id` | `str` | Website ID echoed from the Data Engine |
| `tenant_id` | `str` | Tenant ID from the Data Engine |
| `domain` | `str` | Domain associated with the website |
| `onboarding_status` | `str` | Raw onboarding status string |
| `connector_authenticated` | `bool` | Whether the key was accepted |
| `backend_connected` | `bool` | Whether the backend is connected |
| `message` | `str` | Human-readable summary |

## Checking status without a network call

```python
result = connector.status()
print(result.state.value)   # e.g. "READY"
print(result.ready)         # True / False
```

`status()` returns the last known result without making a network request.
Before the first `connect()` call it returns a `DISCONNECTED` result.

## Repeated connections

`connect()` is idempotent from the Connector's perspective:

```python
# Safe to call on every application startup
result = connector.connect()

# Also safe to call on reconnect after a failure
result = connector.connect()
```

A website already in `READY` state will receive an identical `READY`
response. The Connector never attempts to move the Data Engine onboarding
state backwards.

## Retry behaviour

Transient failures (timeouts, 5xx errors) are automatically retried using
bounded exponential backoff with full jitter. Permanent failures (bad
credentials, website ID mismatch) are never retried.

Default retry settings:
- Max attempts: 3
- Base backoff: 1.0 second
- Cap: 30 seconds
- Algorithm: full jitter

Override the retry policy:

```python
from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.retry import RetryPolicy

connector = TekJuiceConnector(
    max_retries=5,
)

# Or inject a fully custom policy:
policy = RetryPolicy(max_attempts=5, backoff_base=2.0, backoff_cap=60.0)
connector = TekJuiceConnector(retry_policy=policy)
```

## Integration with application startup

Use the generic lifecycle helpers for clean startup/shutdown wiring:

```python
from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.integration.lifecycle import (
    connect_on_startup,
    disconnect_on_shutdown,
)

connector = TekJuiceConnector()

# At app startup:
result = connect_on_startup(connector, raise_on_not_ready=True)

# At app shutdown:
disconnect_on_shutdown(connector)
```

`raise_on_not_ready=True` causes the application to refuse to start if the
Connector cannot reach READY state — useful for environments where a missing
connection should be a hard failure.

## Health checks

```python
from tekjuice_connector.connection.health import check_health

health = check_health(connector._manager)
print(health.healthy)   # True / False
print(health.state)
```

`check_health` never raises — all exceptions are caught and wrapped in a
`HealthResult` with `healthy=False`.
