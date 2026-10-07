"""Public re-exports for the integration subpackage."""

from tekjuice_connector.integration.adapter import ConnectorAdapter
from tekjuice_connector.integration.context import ConnectorContext
from tekjuice_connector.integration.hooks import ConnectorHooks
from tekjuice_connector.integration.lifecycle import (
    connect_on_startup,
    disconnect_on_shutdown,
)

__all__ = [
    "ConnectorAdapter",
    "ConnectorContext",
    "ConnectorHooks",
    "connect_on_startup",
    "disconnect_on_shutdown",
]
