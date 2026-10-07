# Changelog

All notable changes to the Tek Juice Data Engine Connector are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [0.1.0] — 2026-10-07

### Added

- `TekJuiceConnector` public class with `connect()`, `status()`, and `diagnostics()`.
- Configuration loading from environment variables (`TEKJUICE_WEBSITE_ID`, `TEKJUICE_CONNECTOR_KEY`, `TEKJUICE_DATA_ENGINE_URL`).
- Explicit Python configuration support — keyword arguments override environment variables.
- Optional `.env` file loading via `python-dotenv` (not required).
- E2E handshake against `POST /api/v1/connector/handshake` with full response validation.
- Typed models: `ConnectorCredentials`, `HandshakeRequest`, `HandshakeResponse`, `ConnectionResult`, `ConnectionState`, `AuthSession`.
- Full exception hierarchy: `TekJuiceConnectorError` and all subclasses.
- Secret redaction: connector key never appears in `repr()`, logs, exceptions, or diagnostic output.
- `SecretStr` wrapper for safe secret handling.
- HTTPS enforcement with `allow_http` development override.
- Bounded exponential backoff with full jitter (`RetryPolicy`, configurable `max_attempts` capped at 10).
- Permanent vs transient failure distinction — auth failures are never retried.
- `SanitizingFilter` for Python `logging` — auto-attached to the package logger.
- `Diagnostician` / `DiagnosticReport` with configuration and connectivity checks.
- CLI: `tekjuice status`, `tekjuice test-connection`, `tekjuice diagnostics`, `tekjuice --version`.
- Framework-neutral integration layer: `ConnectorHooks`, `ConnectorContext`, `ConnectorAdapter` protocol, `connect_on_startup`, `disconnect_on_shutdown`.
- Connection health check via `check_health()` (non-throwing).
- 188 automated tests across unit, integration, and contract suites.
- Full documentation in `docs/`.
- Examples in `examples/`.

### Security

- Connector key never included in any log output, exception message, repr(), or diagnostic report.
- `SanitizingFilter` scrubs long token-like values from all log records.
- URL security validation: no embedded credentials, HTTPS required in production.

### Notes

- This release has not been tested against a production Tek Juice Data Engine.
  Tests use a mocked HTTP transport.
- M2M authentication is explicitly out of scope and not implemented.
