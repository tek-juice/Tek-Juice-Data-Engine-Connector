# Basic examples

These examples demonstrate the minimal Connector usage pattern.

## Prerequisites

Set the required environment variables:

```bash
export TEKJUICE_WEBSITE_ID=your-website-id
export TEKJUICE_CONNECTOR_KEY=your-connector-key
export TEKJUICE_DATA_ENGINE_URL=https://engine.tekjuice.io
```

Never hard-code real credentials.

## connect.py

Connects to the Data Engine and reports whether the Connector is READY.

```bash
python examples/basic/connect.py
```

## diagnostics.py

Runs the full diagnostic suite and prints the report.

```bash
python examples/basic/diagnostics.py
```
