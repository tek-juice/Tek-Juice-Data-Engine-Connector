# Tek Juice Data Engine Connector

Connects a customer backend to the Tek Juice Data Engine for S2S
publication, whatever the backend is written in:

- **Any language or framework** (Node.js, PHP, Ruby, Go, Java, .NET, ...): run
  the connector as a **sidecar** service (`tekjuice serve`, Docker image). It
  owns the receiver secret, endpoint, registration, verification and readiness,
  and hands content to four small JSON endpoints your backend implements. See
  [docs/sidecar.md](docs/sidecar.md) and the
  [content API contract](docs/content-api.openapi.yaml).
- **Python backends** can also embed it (`pip install tekjuice-connector`) and
  mount the receiver in FastAPI, Flask or Django ([docs/setup.md](docs/setup.md)).

## Receiving publications (0.2.0+)

Mount a `PublicationReceiver` over your own content model with the adapter for
your framework (FastAPI, Flask, Django, or any WSGI/ASGI app) and register it
through the handshake. The Data Engine verifies the endpoint with a dry run
before it publishes anything. See [docs/publication.md](docs/publication.md)
and the wire contract in [docs/publication-contract.md](docs/publication-contract.md).

## Setup: secret, endpoint, registration, readiness (0.7.0+)

`TekJuiceConnector().setup(receiver=..., app=..., secret_store=...)` (or
`tekjuice setup --env-file .env ...`) does the S2S integration itself:

- it generates `TEKJUICE_RECEIVER_SECRET` on your server with
  `secrets.token_urlsafe(48)`, only when none is stored, and persists it safely;
- it builds `TEKJUICE_PUBLICATION_ENDPOINT_URL` from your API's configured host
  and the receiver route, mounting the route on FastAPI/Flask when needed;
- it proves through the public URL, with a dry run, that the endpoint reaches
  your receiver;
- it registers both values with the Data Engine, confirms the Data Engine
  stored them, and waits for the Data Engine's verification;
- it reports readiness stage by stage, publishes nothing, and supports
  coordinated secret rotation.

See [docs/setup.md](docs/setup.md).

## What it does

- Authenticates your backend with the Tek Juice Data Engine via an E2E handshake.
- Reports `READY` state when the Data Engine confirms the backend is connected.
- Provides bounded retry with exponential backoff for transient failures.
- Sanitises all log output and diagnostic reports — secrets never leak.
- Provides a CLI for configuration verification and connectivity testing.

## What it does NOT do

This package does not implement content generation, SEO, or any Data
Engine internal logic, and it never stores content itself: publications are
persisted by a `ContentStore` you implement over your own content model.

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

## Two credentials

- The **connector key** (issued by the Data Engine) authenticates this
  backend's handshake with the Data Engine.
- The **receiver secret** (generated on your server by `setup()`, or by you)
  authenticates the Data Engine's S2S publication requests to your receiver.

They are never interchangeable. S2S through this package is the only
publication transport the Data Engine supports; the earlier M2M transport is
retired. See [docs/authentication.md](docs/authentication.md).

## Performance controls

Optional middleware lets the Data Engine apply approved, reversible delivery
settings (static-asset caching, compression) without code changes; see
[docs/performance.md](docs/performance.md).

## Documentation

- [Installation](docs/installation.md)
- [Sidecar for any language](docs/sidecar.md) and its [content API](docs/content-api.openapi.yaml)
- [Setup (secret, endpoint, registration, rotation)](docs/setup.md)
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
