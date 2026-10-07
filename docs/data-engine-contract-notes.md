# Data Engine Contract Notes

This file records any observations about the Data Engine API contract that
arose during Connector implementation. It does not modify the Data Engine.

---

## Idempotency of repeated handshakes on READY websites

**Observation:** The Connector calls `POST /api/v1/connector/handshake` on
every `connect()` invocation, including when the website is already in
`READY` state (e.g. on backend restart).

**Required Data Engine behaviour:** A valid handshake against a website
already in `READY` state must return the same `READY` response rather than
attempting an invalid state transition (e.g. rejecting the request as
"already onboarded" or returning a conflicting state).

**Connector behaviour:** The Connector accepts and handles a `READY` response
at any time. It never attempts to move the Data Engine state backwards.

**Status:** This is the expected and confirmed contract. The Connector
implementation assumes this is correctly handled by the Data Engine.

---

## No additional endpoints used

The Connector uses only one Data Engine endpoint:

```
POST /api/v1/connector/handshake
```

No other endpoints are called. If future versions of the Connector require
additional endpoints (e.g. a status ping, a disconnect notification), they
will be documented here first before implementation.

---

## Response field types

The confirmed response contract specifies:
- `connector_authenticated`, `backend_connected`, `ready` — boolean (`true`/`false`)

The Connector validates these are actual JSON booleans, not strings like
`"true"`. If the Data Engine ever returns these as strings, a
`ResponseValidationError` will be raised. This would be a Data Engine
contract violation requiring a fix on the Data Engine side.
