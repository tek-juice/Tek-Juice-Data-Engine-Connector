"""CLI: ``tekjuice test-connection`` command.

Performs a live E2E handshake against the Data Engine and reports the
result.  This is the primary tool for verifying that the Connector is
correctly configured and can reach the Data Engine.

Exit codes
----------
0  Handshake succeeded and Connector is READY
1  Configuration error (missing / invalid variables)
2  Authentication failure (bad connector key, website ID mismatch)
3  Connectivity failure (timeout, network error)
4  Data Engine not READY (authenticated but onboarding incomplete)
"""

from __future__ import annotations

import sys

from tekjuice_connector.config.settings import load_settings
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.exceptions.authentication import (
    AuthenticationError,
    InvalidCredentialsError,
    WebsiteIdMismatchError,
)
from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.exceptions.connection import (
    ConnectionError as ConnectorConnectionError,
)
from tekjuice_connector.exceptions.connection import ConnectionTimeoutError
from tekjuice_connector.logging.logger import configure_logging
from tekjuice_connector.models.connection import ConnectionState


def run_test_connection(verbose: bool = False) -> int:
    """Execute the test-connection command.

    Returns
    -------
    int
        Exit code.
    """
    configure_logging()

    # --- Load and validate configuration ---
    try:
        settings = load_settings()
    except TekJuiceConnectorError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    url = settings.credentials.data_engine_url
    wid = settings.credentials.website_id
    print(f"Testing connection to {url} (website_id={wid}) …")

    # --- Attempt live handshake ---
    manager = ConnectionManager.from_settings(settings)

    try:
        result = manager.connect()
    except (InvalidCredentialsError, WebsiteIdMismatchError) as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 2
    except AuthenticationError as exc:
        print(f"Authentication error: {exc}", file=sys.stderr)
        return 2
    except ConnectionTimeoutError as exc:
        print(f"Connection timed out: {exc}", file=sys.stderr)
        return 3
    except ConnectorConnectionError as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        return 3
    except TekJuiceConnectorError as exc:
        print(f"Connector error: {exc}", file=sys.stderr)
        return 3

    # --- Report outcome ---
    if result.ready:
        print("OK — Connector is READY.")
        if verbose:
            print(f"  Onboarding status: {result.onboarding_status}")
            if result.domain:
                print(f"  Domain:            {result.domain}")
            if result.tenant_id:
                print(f"  Tenant ID:         {result.tenant_id}")
        return 0

    # Authenticated but not READY yet
    print(
        f"Connector authenticated but not READY. "
        f"Onboarding status: {result.onboarding_status}",
        file=sys.stderr,
    )
    return 4
