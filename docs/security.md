# Security

## Connector key handling

The `TEKJUICE_CONNECTOR_KEY` is a secret. The Connector is designed to
ensure this key never appears in any output.

### What the Connector does to protect the key

| Location | Protection |
|---|---|
| `repr()` / `str()` on credentials | Key replaced with `<redacted>` |
| Log output | `SanitizingFilter` replaces token-like values |
| Exception messages | Key never included |
| Diagnostic reports | Key never included |
| Test fixtures | Placeholder values only — no real keys |
| Request body | Key travels as an HTTP header only |

### Credential repr example

```python
from tekjuice_connector.models.credentials import ConnectorCredentials

creds = ConnectorCredentials(
    website_id="my-site",
    connector_key="sk-secret",
    data_engine_url="https://engine.example.com",
)
print(repr(creds))
# ConnectorCredentials(website_id='my-site', connector_key=<redacted>, ...)
```

### SecretStr

For cases where you need to hold a secret value safely in Python:

```python
from tekjuice_connector.security.secrets import SecretStr

key = SecretStr("sk-abc123")
print(key)           # <redacted>
print(repr(key))     # SecretStr(<redacted>)
print(f"{key}")      # <redacted>
raw = key.reveal()   # Only explicit call returns the value
```

## Log sanitization

All package log output passes through a `SanitizingFilter` that is attached
automatically when the logging module is first imported. It:

- Replaces any string matching the long-token pattern (20+ alphanumeric chars) with a hint.
- Replaces the value of any log record attribute named `connector_key`, `authorization`, `token`, `password`, or `secret`.
- Applies to both the log message template and `args` tuple.

Apply it to your own handlers:

```python
import logging
from tekjuice_connector.logging.sanitization import SanitizingFilter

handler = logging.StreamHandler()
handler.addFilter(SanitizingFilter())
logging.getLogger().addHandler(handler)
```

Or apply to the package logger:

```python
from tekjuice_connector.logging.sanitization import apply_to_package_logger
apply_to_package_logger()
```

## HTTPS requirement

The Data Engine URL must use `https://` in production. Plain HTTP means
the connector key travels unencrypted.

```python
# This raises ValidationError in production
connector = TekJuiceConnector(
    data_engine_url="http://engine.example.com",
)

# This emits a warning only (development/testing)
connector = TekJuiceConnector(
    data_engine_url="http://localhost:8000",
    allow_http=True,
)
```

## URL security checks

The Connector validates the Data Engine URL for two additional issues:

1. **Embedded credentials** — `https://user:pass@host` is rejected.
   Credentials must never be embedded in URLs; use `TEKJUICE_CONNECTOR_KEY`.

2. **Scheme** — Only `http` and `https` are accepted.

## Secret management recommendations

- Store `TEKJUICE_CONNECTOR_KEY` in your platform's secret manager
  (AWS Secrets Manager, GCP Secret Manager, Vault, etc.).
- Do not hard-code keys in source code.
- Do not commit `.env` files containing real keys to version control.
- Rotate keys if you suspect exposure.
- Use the `safe_key_hint` property for diagnostic output that shows only
  the first four characters.

```python
print(connector.settings.credentials.safe_key_hint)
# "sk-a..."
```

## Header redaction utilities

Available for use in your own code:

```python
from tekjuice_connector.security.redaction import (
    redact_headers,
    redact_dict,
    redact_string,
    safe_url,
)

safe_hdrs = redact_headers(response.headers)
safe_env = redact_dict(os.environ)
```

## Reporting security issues

See [SECURITY.md](../SECURITY.md) for the vulnerability disclosure process.
