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

## Publication registration (0.2.0)

The handshake body may include a `publication` object (see
`publication-contract.md`). The Data Engine responds with
`publication_status`, `publication_ready` and `next_action` in addition to
the 0.1.0 fields; the Connector treats them as optional so it keeps working
against Data Engines that predate publication readiness. `ready` is only
`true` once the Data Engine has verified the publication endpoint.

Non-auth 4xx handshake responses (for example `409` when website ownership is
not yet verified, `422` for an invalid publication endpoint) are surfaced as
`ApiError` with the Data Engine's message and are not retried.

The Data Engine calls the customer's publication endpoint; the Connector
does not call any other Data Engine endpoint.

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
