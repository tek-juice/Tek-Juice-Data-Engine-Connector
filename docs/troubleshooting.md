# Troubleshooting

## Run the built-in diagnostics first

The fastest way to diagnose any issue is:

```bash
tekjuice diagnostics
```

This validates your configuration and attempts a live connection, printing
a structured report. The connector key is never printed.

For configuration-only validation (no network call):

```bash
tekjuice diagnostics --config-only
```

---

## Configuration problems

### `MissingConfigurationError: Required configuration variable 'TEKJUICE_WEBSITE_ID' is not set`

One or more required environment variables are missing.

Check:
```bash
echo $TEKJUICE_WEBSITE_ID
echo $TEKJUICE_CONNECTOR_KEY
echo $TEKJUICE_DATA_ENGINE_URL
```

All three must be set. See [configuration.md](configuration.md).

### `ValidationError: TEKJUICE_DATA_ENGINE_URL must use http or https scheme`

The URL is malformed or uses an unsupported scheme. Use `https://` in
production.

### `ValidationError: TEKJUICE_DATA_ENGINE_URL uses plain HTTP`

You are using `http://` without `allow_http=True`. In production always
use `https://`.

### `ValidationError: TEKJUICE_WEBSITE_ID contains invalid characters`

The website ID must contain only letters, digits, hyphens, underscores, and
dots, and must be at most 255 characters.

---

## Authentication problems

### `InvalidCredentialsError: Connector key was rejected (HTTP 401)`

The Data Engine rejected `TEKJUICE_CONNECTOR_KEY`.

Check:
- Is the key correct and not truncated?
- Has the key been rotated or revoked?
- Are you connecting to the correct Data Engine URL?

The key itself is never printed. Use the hint to confirm the right key is loaded:

```python
print(connector.settings.credentials.safe_key_hint)
# "sk-a..."
```

### `WebsiteIdMismatchError: Response website_id does not match configured website_id`

The Data Engine returned a different `website_id` than the one configured.

Check:
- Does `TEKJUICE_WEBSITE_ID` match the ID registered in Tek Juice?
- Are you connecting to the correct Data Engine environment?

### `ResponseValidationError: Handshake response is missing required field(s): ready`

The Data Engine response did not match the expected contract. This is
unusual and may indicate:

- A Data Engine version mismatch.
- A misconfigured proxy stripping or modifying the response.
- A network appliance returning its own error page as HTTP 200.

Note any details in [data-engine-contract-notes.md](data-engine-contract-notes.md)
if you believe this is a Data Engine issue.

---

## Connectivity problems

### `ConnectionTimeoutError: Connection timed out after 10.0s`

The Data Engine did not respond within the configured timeout.

Check:
- Is `TEKJUICE_DATA_ENGINE_URL` correct and reachable from this host?
- Is there a firewall or security group blocking outbound HTTPS?
- Is the Data Engine healthy?

Increase timeouts for slow networks:

```python
connector = TekJuiceConnector(connect_timeout=30.0, read_timeout=60.0)
```

### `RetryExhaustedError: Operation failed after 3 attempt(s)`

All retry attempts were exhausted. The last error is included in the message.

Check:
- Network connectivity to the Data Engine URL.
- Data Engine health status.
- Firewall / proxy configuration.

Increase retries for unreliable networks:

```python
connector = TekJuiceConnector(max_retries=5)
```

### `ConnectionError: Could not connect to Data Engine`

A network-level failure occurred before any HTTP response was received.

Check DNS resolution:
```bash
curl -I https://engine.tekjuice.io
```

---

## Onboarding not READY

### `result.ready is False, onboarding_status is "PENDING"`

The Connector authenticated successfully but the website has not completed
onboarding on the Data Engine side.

This is not a Connector error. The Connector reports what the Data Engine
returns. Complete the Tek Juice onboarding process and then call
`connect()` again.

The Connector will return `READY` once the Data Engine does.

---

## CLI exit codes

| Command | Code | Meaning |
|---|---|---|
| `tekjuice test-connection` | 0 | READY |
| | 1 | Configuration error |
| | 2 | Authentication failure |
| | 3 | Network/connectivity failure |
| | 4 | Authenticated but not READY |
| `tekjuice diagnostics` | 0 | All checks passed, READY |
| | 1 | One or more checks failed |
| | 2 | Configuration invalid |
| `tekjuice status` | 0 | READY |
| | 1 | Not ready |
| | 2 | Not yet connected |

---

## Checking the package version

```bash
tekjuice --version
python3 -c "import tekjuice_connector; print(tekjuice_connector.__version__)"
```

---

## Still stuck?

1. Run `tekjuice diagnostics` and share the output (the connector key is safe — it is never printed).
2. Check the Data Engine status page.
3. Open an issue on the repository with the diagnostic output and steps to reproduce.
