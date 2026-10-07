"""CLI: ``tekjuice status`` command.

Reports the current connection status based on the last known state.
Does NOT attempt a new connection — use ``tekjuice test-connection`` for
a live check.

Exit codes
----------
0  READY
1  Not ready / configuration error
2  Not yet connected (status() called without prior connect())
"""

from __future__ import annotations

import sys

from tekjuice_connector.config.settings import load_settings
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.logging.logger import configure_logging


def run_status(verbose: bool = False) -> int:
    """Execute the status command.

    Returns
    -------
    int
        Exit code.
    """
    configure_logging()

    try:
        settings = load_settings()
    except TekJuiceConnectorError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    manager = ConnectionManager.from_settings(settings)
    result = manager.status()

    if verbose:
        print(f"State:             {result.state.value}")
        print(f"Ready:             {result.ready}")
        if result.onboarding_status:
            print(f"Onboarding status: {result.onboarding_status}")
        if result.domain:
            print(f"Domain:            {result.domain}")
        if result.message:
            print(f"Message:           {result.message}")
    else:
        print(f"Status: {result.state.value}")

    if result.ready:
        return 0
    if result.state.value == "DISCONNECTED":
        return 2
    return 1
