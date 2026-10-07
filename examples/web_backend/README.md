# Web backend integration example

This directory is reserved for illustrative framework-specific wiring
examples.

> **Important:** The Tek Juice Connector itself is completely
> framework-independent. The examples here are illustrations only — they
> show *where* to call the Connector in a typical web framework lifecycle,
> not a production-ready integration.

## Pattern summary

Regardless of framework, the wiring is always:

1. At application startup → call `connector.connect()` (or `connect_on_startup()`).
2. Expose the connector instance for use in request handlers.
3. At application shutdown → call `disconnect_on_shutdown()`.

## Django (illustrative)

```python
# myapp/apps.py
from django.apps import AppConfig
from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.integration.lifecycle import connect_on_startup

class MyAppConfig(AppConfig):
    name = "myapp"
    connector = None

    def ready(self):
        # Called once when Django starts.
        MyAppConfig.connector = TekJuiceConnector()
        connect_on_startup(MyAppConfig.connector, raise_on_not_ready=False)
```

The Connector does not import Django. Django imports the Connector.
Django's ORM, models, signals, and middleware are not involved.

## FastAPI (illustrative)

```python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.integration.lifecycle import connect_on_startup, disconnect_on_shutdown

connector = TekJuiceConnector()

@asynccontextmanager
async def lifespan(app: FastAPI):
    connect_on_startup(connector, raise_on_not_ready=False)
    yield
    disconnect_on_shutdown(connector)

app = FastAPI(lifespan=lifespan)
```

The Connector is synchronous. If you use async frameworks, call
`connector.connect()` in a thread executor or at sync startup time.

## Flask (illustrative)

```python
# app.py
from flask import Flask
from tekjuice_connector import TekJuiceConnector

app = Flask(__name__)
connector = TekJuiceConnector()

with app.app_context():
    connector.connect()
```

These are minimal, illustrative snippets. The Connector does not require
any of these frameworks.
