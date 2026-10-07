# Configuration

## Overview

The Connector requires three pieces of configuration, provided either as
environment variables or as explicit Python arguments.

| Variable | Description | Required |
|---|---|---|
| `TEKJUICE_WEBSITE_ID` | Your Tek Juice website identifier | Yes |
| `TEKJUICE_CONNECTOR_KEY` | Your connector key (treat as a secret) | Yes |
| `TEKJUICE_DATA_ENGINE_URL` | Base URL of the Tek Juice Data Engine API | Yes |

## Environment variables (recommended)

Set the variables in your deployment environment before starting your
application:

```bash
export TEKJUICE_WEBSITE_ID=your-website-id
export TEKJUICE_CONNECTOR_KEY=your-connector-key
export TEKJUICE_DATA_ENGINE_URL=https://engine.tekjuice.io
```

Then use the Connector without any arguments:

```python
from tekjuice_connector import TekJuiceConnector

connector = TekJuiceConnector()
```

## Explicit Python configuration

You can pass values directly, which is useful when reading from a secrets
manager or a custom configuration system:

```python
import os
from tekjuice_connector import TekJuiceConnector

connector = TekJuiceConnector(
    website_id=os.environ["TEKJUICE_WEBSITE_ID"],
    connector_key=secrets_manager.get("tekjuice-connector-key"),
    data_engine_url="https://engine.tekjuice.io",
)
```

Explicit arguments take precedence over environment variables.

## Optional `.env` file loading

If `python-dotenv` is installed, the Connector will attempt to load a
`.env` file automatically when it reads environment variables. This is
best-effort: if `python-dotenv` is absent, the environment is used as-is.

`.env` loading is disabled when `python-dotenv` is not installed. It is
never required.

Never commit a `.env` file containing real credentials to version control.
See `.env.example` for a safe placeholder template.

## Advanced settings

These can be passed as constructor arguments:

```python
connector = TekJuiceConnector(
    website_id="...",
    connector_key="...",
    data_engine_url="https://engine.tekjuice.io",
    connect_timeout=5.0,   # TCP connect timeout in seconds (default: 10)
    read_timeout=20.0,     # HTTP read timeout in seconds (default: 30)
    max_retries=3,         # Max retry attempts for transient failures (default: 3)
    allow_http=False,      # Set True only in local development (default: False)
)
```

## Validation rules

The Connector validates all configuration before any network I/O:

- `TEKJUICE_WEBSITE_ID` — must be non-empty, max 255 characters, letters/digits/hyphens/underscores/dots only.
- `TEKJUICE_CONNECTOR_KEY` — must be non-empty. The value is never logged or printed.
- `TEKJUICE_DATA_ENGINE_URL` — must be a valid `http` or `https` URL with a hostname. Plain HTTP triggers a warning (or error in production mode).

## HTTPS requirement

Production deployments must use `https://` for `TEKJUICE_DATA_ENGINE_URL`.
Using plain HTTP will raise a `ValidationError` unless `allow_http=True` is
explicitly passed (development only).

The connector key is transmitted as an HTTP header. Plain HTTP means the key
travels unencrypted.

## Configuration errors

Missing or invalid configuration raises typed exceptions before any network
call is attempted:

```python
from tekjuice_connector import TekJuiceConnector, MissingConfigurationError, ValidationError

try:
    connector = TekJuiceConnector()
except MissingConfigurationError as exc:
    print(f"Missing: {exc.variable_name}")
except ValidationError as exc:
    print(f"Invalid config: {exc}")
```
