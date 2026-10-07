# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |

## Reporting a vulnerability

Please do **not** report security vulnerabilities through public GitHub issues.

Email: security@tekjuice.io

Include:
- A description of the vulnerability.
- Steps to reproduce.
- Potential impact.
- Any suggested fix, if you have one.

You will receive an acknowledgement within 48 hours and a response within
7 days.

## Scope

Vulnerabilities in scope include:
- Connector key exposure via logs, exceptions, or diagnostic output.
- Credential leakage through `repr()`, `str()`, or format operations.
- Insecure HTTP transmission of the connector key.
- Dependency vulnerabilities in `requests` or other runtime dependencies.
- Authentication bypass in the handshake validation logic.

## Out of scope

- Vulnerabilities in the Tek Juice Data Engine itself (report to Tek Juice directly).
- M2M authentication (not implemented in this package).
- Issues in development tooling (`pytest`, etc.).

## Security design

See [docs/security.md](docs/security.md) for a description of how the
Connector protects the connector key throughout its lifecycle.
