# Authentication

## Two independent authentication systems

> **Critical distinction**: two credentials are involved and they are never
> interchangeable. The **connector key** authenticates this backend to the
> Data Engine (the handshake). The **receiver secret** authenticates the
> Data Engine to this backend's publication receiver (S2S publication).

---

## Connector E2E authentication

The Connector authenticates your backend with the Tek Juice Data Engine
using a **connector key** tied to your website. This is not a user login —
it is a machine-to-machine credential that proves your backend is the
legitimate owner of the configured website.

### What it does

1. Loads `TEKJUICE_WEBSITE_ID`, `TEKJUICE_CONNECTOR_KEY`, `TEKJUICE_DATA_ENGINE_URL`.
2. Sends a POST request to the Data Engine handshake endpoint.
3. Validates the response.
4. Reports `READY` when the Data Engine confirms the backend is connected.

### The handshake request

```
POST /api/v1/connector/handshake
X-Tek-Juice-Connector-Key: <connector_key>
Content-Type: application/json

{
    "website_id": "<website_id>"
}
```

The connector key travels as a header, never in the request body.

### The handshake response

```json
{
    "website_id": "your-website-id",
    "tenant_id": "tenant-abc",
    "domain": "yoursite.com",
    "onboarding_status": "READY",
    "connector_authenticated": true,
    "backend_connected": true,
    "ready": true
}
```

The Connector validates every required field and verifies that the returned
`website_id` matches the configured one.

### What READY means

`ready: true` in the Data Engine response means:

- The connector key was accepted (`connector_authenticated: true`).
- The backend has been connected (`backend_connected: true`).
- The website's onboarding is complete (`onboarding_status: "READY"`).

### Python example

```python
from tekjuice_connector import TekJuiceConnector

connector = TekJuiceConnector()
result = connector.connect()

if result.ready:
    print("Authenticated and READY")
    print("Domain:", result.domain)
else:
    print("Not READY. Status:", result.onboarding_status)
```

---

## Repeated authentication

Backends restart, redeploy, and reconnect. `connector.connect()` is safe to
call repeatedly:

- A website already in `READY` state returns a `READY` response again.
- The Connector never attempts to move the Data Engine onboarding state backwards.
- Each call performs a fresh handshake to confirm the current state.

This means you can safely call `connect()` on every application startup
without fear of breaking an already-configured website.

---

## Authentication failures

| Failure | Exception | Retried? |
|---|---|---|
| Bad connector key | `InvalidCredentialsError` | No |
| Website ID mismatch | `WebsiteIdMismatchError` | No |
| Missing field in response | `ResponseValidationError` | No |
| Connection timeout | `ConnectionTimeoutError` | Yes |
| Server error (5xx) | `ServerError` → `RetryExhaustedError` | Yes |

Permanent failures (bad credentials, mismatched IDs) are never retried.

```python
from tekjuice_connector import (
    TekJuiceConnector,
    InvalidCredentialsError,
    WebsiteIdMismatchError,
    RetryExhaustedError,
)

connector = TekJuiceConnector()

try:
    result = connector.connect()
except InvalidCredentialsError:
    print("Check TEKJUICE_CONNECTOR_KEY.")
except WebsiteIdMismatchError:
    print("Check TEKJUICE_WEBSITE_ID.")
except RetryExhaustedError as exc:
    print(f"Could not connect after {exc.attempts} attempts.")
```

---

## S2S publication authentication (receiver secret)

Publication is initiated by the Data Engine and received by
`tekjuice_connector.publication.PublicationReceiver`:

- You generate the receiver secret (`secrets.token_urlsafe(48)`, at least
  32 characters) and keep it server-side (`TEKJUICE_RECEIVER_SECRET`).
- The connector sends it once per handshake, inside the request authenticated
  by the connector key; the Data Engine stores it encrypted and never
  returns it.
- The Data Engine presents it as `Authorization: Bearer <secret>` on every
  verification and publication request; the receiver compares it in
  constant time.

**Do not use `TEKJUICE_CONNECTOR_KEY` as the receiver secret, or the
receiver secret as the connector key.**

The Data Engine's earlier M2M publication transport is retired: S2S through
this package is the only supported way to receive publications. See
[publication.md](publication.md) and
[publication-contract.md](publication-contract.md).
