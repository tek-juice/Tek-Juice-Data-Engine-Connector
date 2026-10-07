# Installation

## Requirements

- Python 3.11 or later
- `requests` >= 2.28 (installed automatically)

## Install from source

Clone the repository and install with pip:

```bash
git clone https://github.com/your-org/tekjuice-connector.git
cd tekjuice-connector
pip install .
```

## Install for development

```bash
pip install -e ".[dev]"
```

This installs the package in editable mode plus all development dependencies
(pytest, etc.).

## Install from PyPI (future)

Once published, the package will be installable with:

```bash
pip install tekjuice-connector
```

> The package has not been published to PyPI yet.

## Verify the installation

After installation, the `tekjuice` CLI command should be available:

```bash
tekjuice --version
# tekjuice-connector 0.1.0
```

You can also verify the Python import:

```python
from tekjuice_connector import TekJuiceConnector
print(TekJuiceConnector.__module__)
```

## Dependencies

| Package    | Purpose                         | Required |
|------------|---------------------------------|----------|
| `requests` | HTTP communication              | Yes      |
| `python-dotenv` | Optional `.env` file loading | No (optional) |

The Connector does not require Django, FastAPI, Flask, or any other
web framework.

## Uninstall

```bash
pip uninstall tekjuice-connector
```
