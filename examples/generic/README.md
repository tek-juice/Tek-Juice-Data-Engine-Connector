# Generic examples

Framework-neutral patterns that work in any Python backend.

## startup_pattern.py

Demonstrates a module-level singleton pattern for sharing the Connector
across an application, with generic startup/shutdown lifecycle wiring and
optional hooks.

```bash
export TEKJUICE_WEBSITE_ID=your-website-id
export TEKJUICE_CONNECTOR_KEY=your-connector-key
export TEKJUICE_DATA_ENGINE_URL=https://engine.tekjuice.io
python examples/generic/startup_pattern.py
```

## hooks_pattern.py

Demonstrates registering `on_ready`, `on_connected`, `on_failed`, and
`on_any` callbacks using `ConnectorHooks`.

```bash
python examples/generic/hooks_pattern.py
```
