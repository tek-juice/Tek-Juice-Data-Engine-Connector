# Contributing

Thank you for your interest in contributing to the Tek Juice Data Engine Connector.

## Development setup

```bash
git clone https://github.com/tekjuice/tekjuice-connector.git
cd tekjuice-connector
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

Run with coverage:

```bash
pip install pytest-cov
pytest --cov=tekjuice_connector --cov-report=term-missing
```

## Code standards

- Python 3.11+.
- Type hints on all public functions and methods.
- Docstrings on all public modules, classes, and functions.
- No bare `except:` clauses.
- No circular imports.
- No framework imports (Django, FastAPI, Flask, etc.) in the package source.
- No M2M logic.
- No database access.
- Secrets must never appear in `repr()`, logs, exceptions, or diagnostic output.

## Architecture constraints

Do not violate these:

1. The Connector is a **standalone Python package** — no framework dependencies.
2. It communicates with the Data Engine via **HTTP only** — no database connections.
3. **M2M authentication is separate** — do not implement or reference it.
4. The connector key must never be logged, printed, or included in exceptions.
5. Retries must be **bounded** — never create infinite retry loops.
6. All public functions must have type hints.

## Adding a new feature

1. Open an issue to discuss the change before implementing.
2. Write tests first (or alongside the implementation).
3. Ensure `pytest` passes with no failures.
4. Update the relevant `docs/` file.
5. Add an entry to `CHANGELOG.md` under `[Unreleased]`.
6. Submit a pull request.

## Pull request checklist

- [ ] `pytest` passes (188+ tests, 0 failures).
- [ ] No real credentials in any file.
- [ ] New features have tests.
- [ ] Documentation updated.
- [ ] `CHANGELOG.md` updated.
- [ ] `repr()` of any new credential-holding object redacts secrets.

## Reporting bugs

Open a GitHub issue with:
- Python version.
- Package version (`tekjuice --version`).
- Steps to reproduce.
- Expected vs actual behaviour.
- Diagnostic output (`tekjuice diagnostics` — safe to share, key is never printed).

For security issues, see [SECURITY.md](SECURITY.md).
