# Tek Juice Data Engine Connector

A standalone, framework-agnostic Python package that customer backends
install with `pip` to authenticate with the Tek Juice Data Engine.

## What it does

- Authenticates your backend with the Tek Juice Data Engine via an E2E handshake.
- Reports `READY` state when the Data Engine confirms the backend is connected.
- Provides bounded retry with exponential backoff for transient failures.
- Sanitises all log output and diagnostic reports — secrets never leak.
- Provides a CLI for configuration verification and connectivity testing.

## What it does NOT do

This package does not implement content generation, SEO, publication,
M2M authentication, or any Data Engine internal logic. It is a
**connection package only**.

## Quick start

### 1. Install

```bash
pip install tekjuice-connector
```

### 2. Configure

Set the three required environment variables:

```bash
export TEKJUICE_WEBSITE_ID=your-website-id
export TEKJUICE_CONNECTOR_KEY=your-connector-key
export TEKJUICE_DATA_ENGINE_URL=https://engine.tekjuice.io
```

### 3. Connect

```python
from tekjuice_connector import TekJuiceConnector

connector = TekJuiceConnector()
result = connector.connect()

if result.ready:
    print("Tek Juice Data Engine connected")
    print("Domain:", result.domain)
```

### 4. Test the connection

```bash
tekjuice test-connection
```

### 5. Run diagnostics

```bash
tekjuice diagnostics
```

## Requirements

- Python 3.11+
- `requests` >= 2.28

No web framework required.

## Configuration

| Variable | Description |
|---|---|
| `TEKJUICE_WEBSITE_ID` | Your Tek Juice website identifier |
| `TEKJUICE_CONNECTOR_KEY` | Your connector key (treat as a secret) |
| `TEKJUICE_DATA_ENGINE_URL` | Base URL of the Tek Juice Data Engine API |

See [docs/configuration.md](docs/configuration.md) for full details.

## CLI commands

```bash
tekjuice status              # Print current connection state
tekjuice test-connection     # Live E2E handshake test
tekjuice diagnostics         # Full diagnostic report
tekjuice --version           # Print package version
```

## Security

The connector key is never logged, never included in exception messages, and
never appears in diagnostic output. See [docs/security.md](docs/security.md).

## Authentication vs M2M

**Connector E2E authentication** (this package) and **Tek Juice Data Engine
M2M publication authentication** are completely separate systems. This
package does not implement M2M. See [docs/authentication.md](docs/authentication.md).

## Documentation

- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Authentication](docs/authentication.md)
- [Connection lifecycle](docs/connection.md)
- [Integration](docs/integration.md)
- [Security](docs/security.md)
- [Troubleshooting](docs/troubleshooting.md)

## Development

```bash
git clone https://github.com/tekjuice/tekjuice-connector.git
cd tekjuice-connector
pip install -e ".[dev]"
pytest
```

## License

MIT License. See [LICENSE](LICENSE).
