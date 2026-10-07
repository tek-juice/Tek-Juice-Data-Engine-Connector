"""CLI: ``tekjuice diagnostics`` command.

Runs the full diagnostic suite (configuration + live connectivity) and
prints the report.  Never prints the connector key.

Exit codes
----------
0  All checks passed and Connector is READY
1  One or more checks failed
2  Configuration invalid — connectivity check skipped
"""

from __future__ import annotations

import sys

from tekjuice_connector.config.settings import load_settings
from tekjuice_connector.diagnostics.diagnostic import Diagnostician
from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.logging.logger import configure_logging


def run_diagnostics(config_only: bool = False) -> int:
    """Execute the diagnostics command.

    Parameters
    ----------
    config_only:
        When ``True`` skip the live connectivity check.

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
        print("Cannot proceed with diagnostics — fix configuration first.")
        return 2

    include_connectivity = not config_only
    diagnostician = Diagnostician.from_settings(
        settings, include_connectivity=include_connectivity
    )

    try:
        report = diagnostician.run()
    except Exception as exc:  # noqa: BLE001
        print(f"Diagnostic run failed unexpectedly: {exc}", file=sys.stderr)
        return 1

    print(report.to_text())

    return 0 if report.overall_ok else 1
