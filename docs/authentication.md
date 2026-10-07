# Authentication

## Two independent authentication systems

> **Critical distinction**: the Tek Juice platform has two completely
> separate authentication mechanisms. This document covers Connector E2E
> authentication only. M2M publication authentication is a separate
> system managed entirely by the Data Engine and is not part of this package.

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

## M2M publication authentication — NOT part of this package

The Tek Juice Data Engine has a separate M2M mechanism for executing
publication jobs. That mechanism:

- Is initiated by the Data Engine, not the customer backend.
- Uses its own credentials, not the connector key.
- Is not implemented in this package.
- Must not be confused with Connector E2E authentication.

**Do not use `TEKJUICE_CONNECTOR_KEY` for M2M requests.**
**Do not use M2M credentials for the Connector handshake.**

If you have questions about M2M authentication, refer to the Tek Juice
Data Engine documentation.
